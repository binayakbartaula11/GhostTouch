"""
GhostTouch - Gesture-Controlled Volume and Scroll Interface

This module provides a gesture-based control system using MediaPipe for hand tracking
and PyAutoGUI for interacting with system functions. It supports volume adjustment
and scrolling through distinct hand gestures, offering a natural and touch-free user experience.
"""

# Standard library imports
import time
import math
from typing import List, Tuple, Dict, Optional, Union
from collections import deque

# Third-party imports
import cv2
import numpy as np
import pyautogui
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

# Local imports
import hand_tracking_module as htm

# Constants
CAMERA_WIDTH, CAMERA_HEIGHT = 640, 480  # Standard webcam resolution
FINGER_IDS = [4, 8, 12, 16, 20]  # Landmark IDs for fingertips (thumb, index, middle, ring, pinky)
VOLUME_RANGE = (-63.5, 0.0)  # Min and max volume in dB for Windows audio
HAND_RANGE = (50, 200)  # Min and max hand distance for volume control mapping
SCROLL_SPEED_RANGE = (1, 30)  # Scroll speed range for finer control
SCROLL_MOMENTUM_DECAY = 0.92  # Momentum decay factor for smoother scroll deceleration
GESTURE_HISTORY_LENGTH = 10  # Number of frames to keep for gesture stability analysis
THUMB_INDEX_PROXIMITY_THRESHOLD = 80  # Maximum distance to consider thumb and index as "close"
MIN_VOLUME_GESTURE_LENGTH = 40  # Minimum distance between thumb and index for volume gesture

class GestureController:
    """
    Controller for gesture-based volume and scroll control.
    
    This class handles the main application logic for detecting hand gestures
    and controlling system volume and scrolling with gesture distinction
    and smooth scrolling experience.
    """
    
    def __init__(self):
        """Initialize the gesture controller with camera and detector setup."""
        self._setup_camera()
        self._setup_detector()
        self._setup_audio_control()
        self._init_state_variables()
        
    def _setup_camera(self) -> None:
        """Initialize and configure the camera capture with specified dimensions."""
        self.cap = cv2.VideoCapture(0)  # Connect to primary webcam
        self.cap.set(3, CAMERA_WIDTH)  # Set camera width
        self.cap.set(4, CAMERA_HEIGHT)  # Set camera height
        
    def _setup_detector(self) -> None:
        """Initialize the hand detector with high confidence thresholds for gesture stability."""
        self.detector = htm.HandDetector(
            max_hands=1,  # Limit to one hand to prevent confusion
            detection_confidence=0.85,  # Higher threshold for reliable detection
            tracking_confidence=0.8  # High threshold for stable tracking
        )
        
    def _setup_audio_control(self) -> None:
        """Initialize audio control interface using pycaw to access system volume."""
        devices = AudioUtilities.GetSpeakers()  # Get audio output device
        interface = devices.Activate(
            IAudioEndpointVolume._iid_, CLSCTX_ALL, None)  # Activate volume interface
        self.volume = cast(interface, POINTER(IAudioEndpointVolume))  # Cast to proper interface type
        self.volume_range = self.volume.GetVolumeRange()  # Get device-specific volume range
        
    def _init_state_variables(self) -> None:
        """Initialize all state variables used for gesture tracking and control modes."""
        self.previous_time = 0  # For FPS calculation
        self.mode = 'N'  # Initial mode: N (None), Scroll, or Volume
        self.active = False  # Whether the controller is actively controlling something
        self.volume_bar = 400  # Initial volume bar position for visualization
        self.volume_percentage = 0  # Initial volume percentage
        
        # Scroll control variables
        self.scroll_momentum = 0  # Current scroll momentum/velocity
        self.last_scroll_time = time.time()  # Time of last scroll action
        self.scroll_cooldown = 0.025  # Cooldown for smoother scrolling (25ms)
        self.adaptive_scroll_speed = 1.0  # Dynamic scroll speed multiplier that adjusts with usage
        
        # Gesture history tracking for stability
        self.gesture_history = deque(maxlen=GESTURE_HISTORY_LENGTH)  # Recent gesture patterns
        self.finger_distance_history = deque(maxlen=GESTURE_HISTORY_LENGTH)  # Recent finger distances
        self.thumb_index_distance = 0  # Current distance between thumb and index finger
        self.gesture_stability_counter = 0  # Tracks how stable current gesture is
        self.last_stable_gesture = None  # Last confirmed stable gesture
        
        # Mode switching hysteresis to prevent rapid mode toggling
        self.mode_switch_threshold = 5  # Frames needed with same gesture to confirm mode switch
        self.mode_switch_counter = 0  # Counter for consecutive frames with same pending mode
        self.pending_mode = None  # Mode that might be switched to if stable
        
    def detect_fingers(self, landmark_list: List[List[int]]) -> List[int]:
        """
        Detect which fingers are extended based on landmark positions with hand orientation awareness.
        
        Args:
            landmark_list: List of hand landmark positions from MediaPipe.
            
        Returns:
            List of binary values indicating which fingers are extended (1) or folded (0).
        """
        if not landmark_list:
            return []
            
        fingers = []
        
        # Thumb detection requires special handling due to its different movement axis
        thumb_tip = landmark_list[FINGER_IDS[0]]
        thumb_base = landmark_list[FINGER_IDS[0] - 1]
        
        # Determine hand orientation (left/right) using wrist and base of middle finger positions
        hand_direction = "right" if landmark_list[17][1] > landmark_list[5][1] else "left"
        
        # Adjust thumb extension detection based on hand direction
        if hand_direction == "right":
            fingers.append(1 if thumb_tip[1] < thumb_base[1] else 0)  # Right hand: extended if x is smaller
        else:
            fingers.append(1 if thumb_tip[1] > thumb_base[1] else 0)  # Left hand: extended if x is larger
            
        # Other fingers - compare fingertip position to lower knuckle
        for finger_id in FINGER_IDS[1:]:
            finger_tip = landmark_list[finger_id]
            finger_base = landmark_list[finger_id - 2]  # Lower joint of the finger
            fingers.append(1 if finger_tip[2] < finger_base[2] else 0)  # Extended if tip is higher than base
        
        # Calculate and store thumb-index distance for gesture recognition
        if len(landmark_list) > 8:  # Ensure we have both thumb and index landmarks
            self.thumb_index_distance = math.hypot(
                landmark_list[4][1] - landmark_list[8][1],  # X-difference
                landmark_list[4][2] - landmark_list[8][2]   # Y-difference
            )
            self.finger_distance_history.append(self.thumb_index_distance)  # Add to history for smoothing
        
        return fingers
    
    def calculate_scroll_speed(self, landmark_list: List[List[int]]) -> float:
        """
        Calculate scroll speed based on finger distance and gesture dynamics.
        
        Args:
            landmark_list: List of hand landmark positions.
            
        Returns:
            Calculated scroll speed with adaptive adjustments.
        """
        if not landmark_list:
            return 0
            
        # Get index and middle finger positions for distance measurement
        index_tip = landmark_list[8]
        middle_tip = landmark_list[12]
        
        # Calculate Euclidean distance between fingers
        distance = math.hypot(
            index_tip[1] - middle_tip[1],  # X-difference
            index_tip[2] - middle_tip[2]   # Y-difference
        )
        
        # Map finger distance to base scroll speed using interpolation
        base_speed = np.interp(distance, [30, 200], SCROLL_SPEED_RANGE)
        
        # Apply adaptive multiplier that adjusts based on scrolling duration
        adjusted_speed = base_speed * self.adaptive_scroll_speed
        
        # Cap maximum speed to prevent excessive scrolling
        return min(adjusted_speed, SCROLL_SPEED_RANGE[1] * 1.5)
    
    def is_thumb_index_close(self) -> bool:
        """
        Determine if thumb and index finger are in proximity for potential volume control.
        
        Returns:
            True if the fingers are close enough for volume control, False otherwise.
        """
        # Calculate average distance from recent history for stability
        if len(self.finger_distance_history) > 0:
            avg_distance = sum(self.finger_distance_history) / len(self.finger_distance_history)
            return avg_distance < THUMB_INDEX_PROXIMITY_THRESHOLD
        return False
    
    def is_volume_gesture(self, fingers: List[int]) -> bool:
        """
        Detect volume control gesture using finger pattern and positioning.
        
        Args:
            fingers: List indicating which fingers are extended.
            
        Returns:
            True if valid volume control gesture is detected, False otherwise.
        """
        if not fingers or len(fingers) < 5:
            return False
            
        # Check basic finger pattern: thumb + index extended, others closed
        basic_pattern = (fingers[0] == 1 and fingers[1] == 1 and 
                        fingers[2] == 0 and fingers[3] == 0 and fingers[4] == 0)
        
        # Verify proper thumb-index positioning with sufficient distance
        pinch_position = self.thumb_index_distance > MIN_VOLUME_GESTURE_LENGTH
        
        return basic_pattern and pinch_position
    
    def is_scroll_up_gesture(self, fingers: List[int]) -> bool:
        """
        Detect scroll up gesture with conflict prevention.
        
        Args:
            fingers: List indicating which fingers are extended.
            
        Returns:
            True if valid scroll up gesture is detected, False otherwise.
        """
        if not fingers or len(fingers) < 5:
            return False
            
        # Pattern: only index finger extended, all else closed
        basic_pattern = (fingers[0] == 0 and fingers[1] == 1 and 
                        fingers[2] == 0 and fingers[3] == 0 and fingers[4] == 0)
        
        # Prevent confusion with volume gesture by checking thumb-index separation
        not_volume_gesture = self.thumb_index_distance > THUMB_INDEX_PROXIMITY_THRESHOLD
        
        return basic_pattern and not_volume_gesture
    
    def is_scroll_down_gesture(self, fingers: List[int]) -> bool:
        """
        Detect scroll down gesture with two-finger pattern.
        
        Args:
            fingers: List indicating which fingers are extended.
            
        Returns:
            True if valid scroll down gesture is detected, False otherwise.
        """
        if not fingers or len(fingers) < 5:
            return False
            
        # Pattern: index and middle fingers extended, all else closed
        return (fingers[0] == 0 and fingers[1] == 1 and 
                fingers[2] == 1 and fingers[3] == 0 and fingers[4] == 0)
    
    def update_mode(self, fingers: List[int]) -> None:
        """
        Mode switching with hysteresis and gesture stability analysis.
        
        Args:
            fingers: List of binary values indicating which fingers are extended.
        """
        if not fingers:
            self.pending_mode = None
            self.mode_switch_counter = 0
            return
            
        # Create unique gesture key and add to history
        gesture_key = ''.join(map(str, fingers))
        self.gesture_history.append(gesture_key)
        
        # Determine appropriate mode based on current gesture
        if fingers == [0, 0, 0, 0, 0]:  # Fist/closed hand - Reset mode
            new_mode = 'N'
        elif self.is_scroll_up_gesture(fingers) or self.is_scroll_down_gesture(fingers):
            new_mode = 'Scroll'
        elif self.is_volume_gesture(fingers):
            new_mode = 'Volume'
        else:
            new_mode = self.mode  # Maintain current mode if gesture not recognized
        
        # Mode switching with hysteresis to prevent accidental mode changes
        if new_mode != self.pending_mode:
            # Reset counter when pending mode changes
            self.pending_mode = new_mode
            self.mode_switch_counter = 0
        else:
            # Increment stability counter for consistent gesture
            self.mode_switch_counter += 1
            
            # Only switch mode after stable period of the same gesture
            if self.mode_switch_counter >= self.mode_switch_threshold and new_mode != self.mode:
                self._switch_mode(new_mode)
    
    def _switch_mode(self, new_mode: str) -> None:
        """
        Handle mode transition with appropriate initialization and parameter resets.
        
        Args:
            new_mode: Mode to switch to ('N', 'Scroll', or 'Volume')
        """
        old_mode = self.mode
        self.mode = new_mode
        
        # Initialize the new mode
        if new_mode == 'N':
            self._reset_mode()
        elif new_mode == 'Scroll':
            self._set_scroll_mode()
        elif new_mode == 'Volume':
            self._set_volume_mode()
            
        # Reset adaptive parameters when changing modes to provide clean state
        if old_mode != new_mode:
            self.adaptive_scroll_speed = 1.0
            self.scroll_momentum = 0
            
    def _reset_mode(self) -> None:
        """Reset the controller to neutral/idle mode with default parameters."""
        self.active = False
        self.scroll_momentum = 0
        
    def _set_scroll_mode(self) -> None:
        """Set the controller to scroll mode and activate scrolling functionality."""
        self.active = True
        
    def _set_volume_mode(self) -> None:
        """Set the controller to volume mode and activate volume control functionality."""
        self.active = True
        
    def handle_scroll_mode(self, fingers: List[int], landmark_list: List[List[int]], img: cv2.Mat) -> None:
        """
        Scroll handling with speed adaptation and momentum physics.
        
        Args:
            fingers: List of binary values indicating which fingers are extended.
            landmark_list: List of hand landmark positions.
            img: Image to draw visual feedback on.
        """
        current_time = time.time()
        
        # Adaptive speed adjustment based on continuous scrolling
        time_scrolling = current_time - self.last_scroll_time
        if time_scrolling < 0.5:  # If actively scrolling (within last 500ms)
            # Gradually increase speed for sustained scrolling (up to 50% faster)
            self.adaptive_scroll_speed = min(self.adaptive_scroll_speed * 1.01, 1.5)
        else:
            # Reset to default speed when not actively scrolling
            self.adaptive_scroll_speed = 1.0
        
        # Respect scrolling cooldown while still applying momentum
        if current_time - self.last_scroll_time < self.scroll_cooldown:
            # Apply momentum even during cooldown for smoother transition
            self._handle_scroll_momentum()
            return
            
        # Calculate new scroll speed based on current finger positions
        scroll_speed = self.calculate_scroll_speed(landmark_list)
        
        # Dynamic momentum physics based on scroll speed
        momentum_modifier = np.interp(scroll_speed, 
                                    SCROLL_SPEED_RANGE, 
                                    [0.9, 0.95])  # Higher speed = more momentum
        
        # Process specific scroll gestures with dynamic feedback
        if self.is_scroll_up_gesture(fingers):  # Scroll up gesture detected
            self._handle_scroll_up(scroll_speed, img)
            SCROLL_MOMENTUM_DECAY = momentum_modifier  # Update momentum decay rate
        elif self.is_scroll_down_gesture(fingers):  # Scroll down gesture detected
            self._handle_scroll_down(scroll_speed, img)
            SCROLL_MOMENTUM_DECAY = momentum_modifier  # Update momentum decay rate
        else:
            # Apply existing momentum if no active scroll gesture
            self._handle_scroll_momentum()
                
        self.last_scroll_time = current_time  # Update timestamp for cooldown
        self._draw_scroll_feedback(img)  # Update visual feedback
        
    def _handle_scroll_up(self, speed: float, img: cv2.Mat) -> None:
        """
        Process upward scrolling action with visual feedback.
        
        Args:
            speed: Calculated scroll speed.
            img: Image for visual feedback.
        """
        self.put_text('U', (200, 455), (0, 255, 0), img)  # Show 'Up' indicator
        
        # Apply calculated speed directly for immediate response
        self.scroll_momentum = speed
        
        # Execute scroll with integer value for consistent behavior
        scroll_amount = int(self.scroll_momentum)
        if scroll_amount > 0:
            pyautogui.scroll(scroll_amount)  # Positive = scroll up
        
    def _handle_scroll_down(self, speed: float, img: cv2.Mat) -> None:
        """
        Process downward scrolling action with visual feedback.
        
        Args:
            speed: Calculated scroll speed.
            img: Image for visual feedback.
        """
        self.put_text('D', (200, 455), (0, 0, 255), img)  # Show 'Down' indicator
        
        # Apply negative speed for downward scrolling
        self.scroll_momentum = -speed
        
        # Execute scroll with integer value for consistent behavior
        scroll_amount = int(self.scroll_momentum)
        if scroll_amount < 0:
            pyautogui.scroll(scroll_amount)  # Negative = scroll down
        
    def _handle_scroll_momentum(self) -> None:
        """Apply momentum physics to scrolling with decay."""
        if abs(self.scroll_momentum) > 0.5:  # Only apply if momentum is significant
            # Apply momentum decay factor for natural deceleration
            self.scroll_momentum *= SCROLL_MOMENTUM_DECAY
            
            # Convert to integer for consistent scrolling behavior
            scroll_amount = int(round(self.scroll_momentum))
            
            # Only execute scroll if magnitude is meaningful
            if abs(scroll_amount) >= 1:
                pyautogui.scroll(scroll_amount)
        else:
            # Stop momentum completely when below threshold
            self.scroll_momentum = 0
            
    def _draw_scroll_feedback(self, img: cv2.Mat) -> None:
        """
        Render visual feedback for scroll speed and momentum.
        
        Args:
            img: Image to draw feedback on.
        """
        # Calculate visual bar length based on momentum magnitude
        speed_bar_length = int(np.interp(abs(self.scroll_momentum), 
                                       [0, SCROLL_SPEED_RANGE[1]], 
                                       [0, 200]))
        
        # Draw speed indicator bar
        cv2.rectangle(img, (200, 410), (200 + speed_bar_length, 430), (0, 255, 0), cv2.FILLED)
        cv2.rectangle(img, (200, 410), (400, 430), (255, 255, 255), 2)  # Outline
        
        # Display numeric speed and adaptive multiplier
        cv2.putText(img, f'Speed: {abs(self.scroll_momentum):.1f} (x{self.adaptive_scroll_speed:.2f})', 
                   (200, 405), cv2.FONT_HERSHEY_COMPLEX_SMALL, 0.8, (255, 255, 255), 1)
    
    def handle_volume_mode(self, fingers: List[int], landmark_list: List[List[int]], img: cv2.Mat) -> None:
        """
        Volume control with gesture verification and visual feedback.
        
        Args:
            fingers: List of binary values indicating which fingers are extended.
            landmark_list: List of hand landmark positions.
            img: Image to draw visual feedback on.
        """
        # Check for exit gesture (pinky finger extended)
        if fingers[-1] == 1:  
            self._reset_mode()  # Exit volume mode if pinky is raised
            return
            
        # Only update volume if still in proper volume gesture
        if self.is_volume_gesture(fingers):
            self._update_volume(landmark_list, img)
        else:
            # Maintain visual display without changing volume
            self._draw_volume_display(img)
        
    def _update_volume(self, landmark_list: List[List[int]], img: cv2.Mat) -> None:
        """
        Update system volume based on finger positions with smooth transitions.
        
        Args:
            landmark_list: List of hand landmark positions.
            img: Image to draw visual feedback on.
        """
        # Get thumb and index finger tip positions
        x1, y1 = landmark_list[4][1], landmark_list[4][2]  # Thumb tip
        x2, y2 = landmark_list[8][1], landmark_list[8][2]  # Index tip
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2  # Center point between fingers
        
        # Draw visual connection between control fingers
        self._draw_finger_connection(x1, y1, x2, y2, cx, cy, img)
        
        # Calculate current finger distance for volume mapping
        length = math.hypot(x2 - x1, y2 - y1)
        
        # Apply temporal smoothing using weighted moving average
        if len(self.finger_distance_history) > 0:
            # 70% current value + 30% historical average for stability
            smoothed_length = 0.7 * length + 0.3 * (sum(self.finger_distance_history) / len(self.finger_distance_history))
        else:
            smoothed_length = length
        
        # Map distance to volume level with proper range conversion
        vol = np.interp(smoothed_length, [HAND_RANGE[0], HAND_RANGE[1]], [VOLUME_RANGE[0], VOLUME_RANGE[1]])
        
        # Apply volume change to system
        self.volume.SetMasterVolumeLevel(vol, None)
        
        # Update visual indicators with smoothed values
        self._update_volume_feedback(smoothed_length, vol, img)
        
    def _draw_finger_connection(self, x1: int, y1: int, x2: int, y2: int, cx: int, cy: int, img: cv2.Mat) -> None:
        """
        Draw visualization of finger positioning for volume control.
        
        Args:
            x1, y1: Coordinates of first finger tip (thumb).
            x2, y2: Coordinates of second finger tip (index).
            cx, cy: Coordinates of center point between fingers.
            img: Image to draw on.
        """
        # Draw finger tip indicators
        cv2.circle(img, (x1, y1), 12, (0, 215, 255), cv2.FILLED)  # Thumb
        cv2.circle(img, (x2, y2), 12, (0, 215, 255), cv2.FILLED)  # Index
        
        # Draw connecting line with thickness based on finger distance
        distance = math.hypot(x2 - x1, y2 - y1)
        thickness = int(np.interp(distance, [50, 200], [2, 6]))  # Thicker line for greater distance
        cv2.line(img, (x1, y1), (x2, y2), (0, 215, 255), thickness)
        
        # Draw center control point with size based on distance/volume
        center_radius = int(np.interp(distance, [50, 200], [5, 15]))
        cv2.circle(img, (cx, cy), center_radius, (0, 215, 255), cv2.FILLED)
        
        # Add highlight indicators for volume extremes
        if distance < 50:  # Near minimum volume
            cv2.circle(img, (cx, cy), center_radius + 5, (0, 0, 255), 2)  # Red indicator
        elif distance > 190:  # Near maximum volume
            cv2.circle(img, (cx, cy), center_radius + 5, (0, 255, 0), 2)  # Green indicator
        
    def _update_volume_feedback(self, length: float, vol: float, img: cv2.Mat) -> None:
        """
        Update volume visualization parameters based on current settings.
        
        Args:
            length: Current finger distance.
            vol: Current volume level in dB.
            img: Image to update feedback on.
        """
        # Convert volume to visual bar height (higher volume = higher bar)
        self.volume_bar = np.interp(vol, [VOLUME_RANGE[0], VOLUME_RANGE[1]], [400, 150])
        # Convert volume to percentage for display
        self.volume_percentage = np.interp(vol, [VOLUME_RANGE[0], VOLUME_RANGE[1]], [0, 100])
        
        # Render the volume display
        self._draw_volume_display(img)
    
    def _draw_volume_display(self, img: cv2.Mat) -> None:
        """
        Draw the complete volume visualization with percentage and level indicators.
        
        Args:
            img: Image to draw on.
        """
        # Draw volume bar background/container
        cv2.rectangle(img, (30, 150), (55, 400), (209, 206, 0), 3)
        
        # Draw filled portion representing current volume
        cv2.rectangle(img, (30, int(self.volume_bar)), (55, 400), (215, 255, 127), cv2.FILLED)
        
        # Add percentage text indicator
        cv2.putText(img, f'{int(self.volume_percentage)}%', (25, 430), 
                   cv2.FONT_HERSHEY_COMPLEX, 0.9, (209, 206, 0), 3)
        
        # Add level markers at regular intervals
        for level in range(0, 101, 20):  # 0%, 20%, 40%, 60%, 80%, 100%
            y_pos = int(np.interp(level, [0, 100], [400, 150]))
            cv2.rectangle(img, (55, y_pos), (65, y_pos), (209, 206, 0), 2)
            
            # Add text labels for major levels
            if level % 40 == 0:  # Show 0%, 40%, 80% to avoid visual clutter
                cv2.putText(img, f'{level}%', (70, y_pos+5), 
                           cv2.FONT_HERSHEY_PLAIN, 1, (209, 206, 0), 1)
    
    def put_text(self, 
                text: str, 
                position: Tuple[int, int] = (250, 450), 
                color: Tuple[int, int, int] = (0, 255, 255),
                img: cv2.Mat = None) -> None:
        """
        Helper method to display text on images with consistent formatting.
        
        Args:
            text: Text string to display.
            position: (x, y) coordinates for text placement.
            color: RGB color tuple for the text.
            img: Image to write text on.
        """
        cv2.putText(img, text, position, cv2.FONT_HERSHEY_COMPLEX_SMALL, 3, color, 3)
    
    def run(self) -> None:
        """Main application loop with gesture processing and visualization."""
        while True:
            # Capture frame from camera
            success, img = self.cap.read()
            if not success:
                break  # Exit if camera capture fails
                
            # Process hand tracking
            img = self.detector.find_hands(img)  # Detect and draw hand landmarks
            landmark_list = self.detector.find_position(img, draw=False)  # Get landmark coordinates
            
            if landmark_list:
                # Process detected hand
                fingers = self.detect_fingers(landmark_list)  # Determine finger positions
                
                # Update control mode based on detected gestures
                self.update_mode(fingers)
                
                # Handle active control mode with appropriate action
                if self.mode == 'Scroll':
                    self.put_text('Scroll', img=img)  # Display current mode
                    self.handle_scroll_mode(fingers, landmark_list, img)  # Process scroll gestures
                elif self.mode == 'Volume':
                    self.put_text('Volume', img=img)  # Display current mode
                    self.handle_volume_mode(fingers, landmark_list, img)  # Process volume gestures
                else:
                    self.put_text('Idle', img=img)  # Display idle state
                    
                # Display finger status for debugging/feedback
                fingers_text = ''.join(map(str, fingers)) if fingers else "No fingers"
                cv2.putText(img, fingers_text, (10, 30), cv2.FONT_HERSHEY_PLAIN, 1.5, (255, 0, 255), 2)
                
                # Display thumb-index distance for debugging
                dist_text = f"T-I: {int(self.thumb_index_distance)}"
                cv2.putText(img, dist_text, (10, 60), cv2.FONT_HERSHEY_PLAIN, 1.5, (255, 0, 255), 2)
            
            # Calculate and display performance metrics
            current_time = time.time()
            fps = 1 / (current_time - self.previous_time)  # Calculate frames per second
            self.previous_time = current_time
            cv2.putText(img, f'FPS:{int(fps)}', (480, 50), cv2.FONT_ITALIC, 1, (255, 0, 0), 2)
            
            # Display the processed frame
            cv2.imshow('Gesture Control', img)
            
            # Check for exit command (q key)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
        # Clean up resources
        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    controller = GestureController()  # Initialize the controller
    controller.run()  # Start the main application loop
