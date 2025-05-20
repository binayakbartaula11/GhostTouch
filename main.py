"""
GhostTouch - Gesture-Controlled Volume and Scroll Interface

This module implements a gesture-based control system using MediaPipe for hand tracking
and PyAutoGUI for system control. It provides volume control and smooth scrolling
functionality through hand gestures.
"""

# Standard library imports
import time
import math
from typing import List, Tuple

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
CAMERA_WIDTH, CAMERA_HEIGHT = 640, 480
FINGER_IDS = [4, 8, 12, 16, 20]  # Thumb, Index, Middle, Ring, Pinky
VOLUME_RANGE = (-63.5, 0.0)  # Min and max volume in dB
HAND_RANGE = (50, 200)  # Min and max hand distance for volume control
SCROLL_SPEED_RANGE = (1, 20)  # Min and max scroll speed
SCROLL_MOMENTUM_DECAY = 0.95  # Decay factor for scroll momentum

class GestureController:
    """
    Controller for gesture-based volume and scroll control.
    
    This class handles the main application logic for detecting hand gestures
    and controlling system volume and scrolling. It uses MediaPipe for hand
    tracking and PyAutoGUI for system control.
    """
    
    def __init__(self):
        """Initialize the gesture controller with camera and detector setup."""
        self._setup_camera()
        self._setup_detector()
        self._setup_audio_control()
        self._init_state_variables()
        
    def _setup_camera(self) -> None:
        """Initialize and configure the camera capture."""
        self.cap = cv2.VideoCapture(0)
        self.cap.set(3, CAMERA_WIDTH)
        self.cap.set(4, CAMERA_HEIGHT)
        
    def _setup_detector(self) -> None:
        """Initialize the hand detector with appropriate confidence thresholds."""
        self.detector = htm.HandDetector(
            max_hands=1,
            detection_confidence=0.85,
            tracking_confidence=0.8
        )
        
    def _setup_audio_control(self) -> None:
        """Initialize audio control using pycaw."""
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(
            IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        self.volume = cast(interface, POINTER(IAudioEndpointVolume))
        self.volume_range = self.volume.GetVolumeRange()
        
    def _init_state_variables(self) -> None:
        """Initialize all state variables used in the controller."""
        self.previous_time = 0
        self.mode = 'N'  # N: None, Scroll, Volume
        self.active = False
        self.volume_bar = 400
        self.volume_percentage = 0
        self.scroll_momentum = 0
        self.last_scroll_time = time.time()
        self.scroll_cooldown = 0.05  # 50ms cooldown between scrolls
        
    def detect_fingers(self, landmark_list: List[List[int]]) -> List[int]:
        """
        Detect which fingers are extended based on landmark positions.
        
        Args:
            landmark_list: List of hand landmark positions.
            
        Returns:
            List of binary values indicating which fingers are extended (1) or folded (0).
        """
        if not landmark_list:
            return []
            
        fingers = []
        
        # Thumb detection (different for left/right hand)
        thumb_base = landmark_list[FINGER_IDS[0] - 1]
        thumb_tip = landmark_list[FINGER_IDS[0]]
        fingers.append(1 if thumb_tip[1] >= thumb_base[1] else 0)
            
        # Other fingers
        for finger_id in FINGER_IDS[1:]:
            finger_tip = landmark_list[finger_id]
            finger_base = landmark_list[finger_id - 2]
            fingers.append(1 if finger_tip[2] < finger_base[2] else 0)
            
        return fingers
    
    def calculate_scroll_speed(self, landmark_list: List[List[int]]) -> float:
        """
        Calculate scroll speed based on finger distance.
        
        Args:
            landmark_list: List of hand landmark positions.
            
        Returns:
            Calculated scroll speed based on finger distance.
        """
        if not landmark_list:
            return 0
            
        # Get index and middle finger positions
        index_tip = landmark_list[8]
        middle_tip = landmark_list[12]
        
        # Calculate distance between fingers
        distance = math.hypot(
            index_tip[1] - middle_tip[1],
            index_tip[2] - middle_tip[2]
        )
        
        # Map distance to scroll speed
        return np.interp(distance, [30, 200], SCROLL_SPEED_RANGE)
    
    def update_mode(self, fingers: List[int]) -> None:
        """
        Update the current control mode based on finger positions.
        
        Args:
            fingers: List of binary values indicating which fingers are extended.
        """
        if not fingers:
            return
            
        if fingers == [0, 0, 0, 0, 0]:  # Fist - Reset mode
            self._reset_mode()
        elif (fingers == [0, 1, 0, 0, 0] or fingers == [0, 1, 1, 0, 0]) and not self.active:
            self._set_scroll_mode()
        elif (fingers == [1, 1, 0, 0, 0]) and not self.active:
            self._set_volume_mode()
            
    def _reset_mode(self) -> None:
        """Reset the controller to neutral mode."""
        self.mode = 'N'
        self.active = False
        self.scroll_momentum = 0
        
    def _set_scroll_mode(self) -> None:
        """Set the controller to scroll mode."""
        self.mode = 'Scroll'
        self.active = True
        
    def _set_volume_mode(self) -> None:
        """Set the controller to volume mode."""
        self.mode = 'Volume'
        self.active = True
    
    def handle_scroll_mode(self, fingers: List[int], landmark_list: List[List[int]], img: cv2.Mat) -> None:
        """
        Handle scroll mode functionality with smooth scrolling.
        
        Args:
            fingers: List of binary values indicating which fingers are extended.
            landmark_list: List of hand landmark positions.
            img: Image to draw visual feedback on.
        """
        current_time = time.time()
        
        # Check if enough time has passed since last scroll
        if current_time - self.last_scroll_time < self.scroll_cooldown:
            return
            
        # Calculate scroll speed based on finger distance
        scroll_speed = self.calculate_scroll_speed(landmark_list)
        
        if fingers == [0, 1, 0, 0, 0]:  # Scroll up
            self._handle_scroll_up(scroll_speed, img)
        elif fingers == [0, 1, 1, 0, 0]:  # Scroll down
            self._handle_scroll_down(scroll_speed, img)
        else:
            self._handle_scroll_momentum()
                
        self.last_scroll_time = current_time
        self._draw_scroll_feedback(img)
        
    def _handle_scroll_up(self, speed: float, img: cv2.Mat) -> None:
        """Handle upward scrolling."""
        self.put_text('U', (200, 455), (0, 255, 0), img)
        self.scroll_momentum = speed
        pyautogui.scroll(int(self.scroll_momentum))
        
    def _handle_scroll_down(self, speed: float, img: cv2.Mat) -> None:
        """Handle downward scrolling."""
        self.put_text('D', (200, 455), (0, 0, 255), img)
        self.scroll_momentum = -speed
        pyautogui.scroll(int(self.scroll_momentum))
        
    def _handle_scroll_momentum(self) -> None:
        """Handle scroll momentum when no gesture is detected."""
        if abs(self.scroll_momentum) > 0.1:
            pyautogui.scroll(int(self.scroll_momentum))
            self.scroll_momentum *= SCROLL_MOMENTUM_DECAY
        else:
            self.scroll_momentum = 0
            
    def _draw_scroll_feedback(self, img: cv2.Mat) -> None:
        """Draw scroll speed indicator and feedback on the image."""
        speed_bar_length = int(np.interp(abs(self.scroll_momentum), [0, SCROLL_SPEED_RANGE[1]], [0, 200]))
        cv2.rectangle(img, (200, 410), (200 + speed_bar_length, 430), (0, 255, 0), cv2.FILLED)
        cv2.rectangle(img, (200, 410), (400, 430), (255, 255, 255), 2)
        cv2.putText(img, f'Speed: {abs(self.scroll_momentum):.1f}', (200, 405), 
                    cv2.FONT_HERSHEY_COMPLEX_SMALL, 0.8, (255, 255, 255), 1)
    
    def handle_volume_mode(self, fingers: List[int], landmark_list: List[List[int]], img: cv2.Mat) -> None:
        """
        Handle volume control mode functionality.
        
        Args:
            fingers: List of binary values indicating which fingers are extended.
            landmark_list: List of hand landmark positions.
            img: Image to draw visual feedback on.
        """
        if fingers[-1] == 1:  # Pinky finger extended to exit
            self._reset_mode()
            return
            
        self._update_volume(landmark_list, img)
        
    def _update_volume(self, landmark_list: List[List[int]], img: cv2.Mat) -> None:
        """Update system volume based on finger positions."""
        # Get thumb and index finger positions
        x1, y1 = landmark_list[4][1], landmark_list[4][2]
        x2, y2 = landmark_list[8][1], landmark_list[8][2]
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        
        # Draw finger connection
        self._draw_finger_connection(x1, y1, x2, y2, cx, cy, img)
        
        # Calculate and set volume
        length = math.hypot(x2 - x1, y2 - y1)
        vol = np.interp(length, [HAND_RANGE[0], HAND_RANGE[1]], [VOLUME_RANGE[0], VOLUME_RANGE[1]])
        self.volume.SetMasterVolumeLevel(vol, None)
        
        # Update visual feedback
        self._update_volume_feedback(length, vol, img)
        
    def _draw_finger_connection(self, x1: int, y1: int, x2: int, y2: int, cx: int, cy: int, img: cv2.Mat) -> None:
        """Draw the connection between fingers for volume control."""
        cv2.circle(img, (x1, y1), 10, (0, 215, 255), cv2.FILLED)
        cv2.circle(img, (x2, y2), 10, (0, 215, 255), cv2.FILLED)
        cv2.line(img, (x1, y1), (x2, y2), (0, 215, 255), 3)
        cv2.circle(img, (cx, cy), 8, (0, 215, 255), cv2.FILLED)
        
    def _update_volume_feedback(self, length: float, vol: float, img: cv2.Mat) -> None:
        """Update volume bar and percentage display."""
        self.volume_bar = np.interp(vol, [VOLUME_RANGE[0], VOLUME_RANGE[1]], [400, 150])
        self.volume_percentage = np.interp(vol, [VOLUME_RANGE[0], VOLUME_RANGE[1]], [0, 100])
        
        # Draw volume bar
        cv2.rectangle(img, (30, 150), (55, 400), (209, 206, 0), 3)
        cv2.rectangle(img, (30, int(self.volume_bar)), (55, 400), (215, 255, 127), cv2.FILLED)
        cv2.putText(img, f'{int(self.volume_percentage)}%', (25, 430), 
                    cv2.FONT_HERSHEY_COMPLEX, 0.9, (209, 206, 0), 3)
    
    def put_text(self, 
                text: str, 
                position: Tuple[int, int] = (250, 450), 
                color: Tuple[int, int, int] = (0, 255, 255),
                img: cv2.Mat = None) -> None:
        """
        Helper function to put text on the image.
        
        Args:
            text: Text to display.
            position: (x, y) position for the text.
            color: RGB color tuple for the text.
            img: Image to draw text on.
        """
        cv2.putText(img, text, position, cv2.FONT_HERSHEY_COMPLEX_SMALL, 3, color, 3)
    
    def run(self) -> None:
        """Main application loop."""
        while True:
            success, img = self.cap.read()
            if not success:
                break
                
            img = self.detector.find_hands(img)
            landmark_list = self.detector.find_position(img, draw=False)
            
            if landmark_list:
                fingers = self.detect_fingers(landmark_list)
                self.update_mode(fingers)
                
                if self.mode == 'Scroll':
                    self.put_text('Scroll', img=img)
                    self.handle_scroll_mode(fingers, landmark_list, img)
                elif self.mode == 'Volume':
                    self.put_text('Volume', img=img)
                    self.handle_volume_mode(fingers, landmark_list, img)
                else:
                    self.put_text('Idle', img=img)
            
            # Calculate and display FPS
            current_time = time.time()
            fps = 1 / (current_time - self.previous_time)
            self.previous_time = current_time
            cv2.putText(img, f'FPS:{int(fps)}', (480, 50), cv2.FONT_ITALIC, 1, (255, 0, 0), 2)
            
            cv2.imshow('Gesture Control', img)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    controller = GestureController()
    controller.run()