import cv2
import mediapipe as mp
from typing import List, Tuple, Union, Optional

class HandDetector:
    """A class for detecting and tracking hands using MediaPipe."""
    
    def __init__(self, 
                 mode: bool = False, 
                 max_hands: int = 2, 
                 detection_confidence: float = 0.5, 
                 tracking_confidence: float = 0.5) -> None:
        """
        Initialize the hand detector.
        
        Args:
            mode: Whether to treat the input images as a batch of static images.
            max_hands: Maximum number of hands to detect.
            detection_confidence: Minimum confidence value for hand detection.
            tracking_confidence: Minimum confidence value for hand tracking.
        """
        self.mode = mode
        self.max_hands = max_hands
        self.detection_confidence = detection_confidence
        self.tracking_confidence = tracking_confidence

        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=self.mode,
            max_num_hands=self.max_hands,
            min_detection_confidence=self.detection_confidence,
            min_tracking_confidence=self.tracking_confidence
        )
        self.mp_draw = mp.solutions.drawing_utils

    def find_hands(self, 
                  img: cv2.Mat, 
                  draw: bool = True) -> cv2.Mat:
        """
        Find hands in the image.
        
        Args:
            img: Input image in BGR format.
            draw: Whether to draw hand landmarks on the image.
            
        Returns:
            Image with hand landmarks drawn if draw=True.
        """
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        self.results = self.hands.process(img_rgb)

        if self.results.multi_hand_landmarks and draw:
            for hand_landmarks in self.results.multi_hand_landmarks:
                self.mp_draw.draw_landmarks(
                    img, 
                    hand_landmarks,
                    self.mp_hands.HAND_CONNECTIONS
                )
        return img

    def find_position(self, 
                     img: cv2.Mat, 
                     hand_number: int = 0, 
                     draw: bool = True, 
                     color: Tuple[int, int, int] = (255, 0, 255),
                     include_z: bool = False) -> List[List[Union[int, float]]]:
        """
        Find the position of hand landmarks.
        
        Args:
            img: Input image for scaling the landmark positions.
            hand_number: Index of the hand to get landmarks for.
            draw: Whether to draw circles at landmark positions.
            color: Color of the drawn circles.
            include_z: Whether to include z-coordinate in the output.
            
        Returns:
            List of landmarks with their positions.
        """
        landmark_list = []
        
        if self.results.multi_hand_landmarks:
            hand = self.results.multi_hand_landmarks[hand_number]
            
            for landmark_id, landmark in enumerate(hand.landmark):
                height, width, _ = img.shape
                
                if include_z:
                    cx, cy, cz = int(landmark.x * width), int(landmark.y * height), round(landmark.z, 3)
                    landmark_list.append([landmark_id, cx, cy, cz])
                else:
                    cx, cy = int(landmark.x * width), int(landmark.y * height)
                    landmark_list.append([landmark_id, cx, cy])

                if draw:
                    cv2.circle(img, (cx, cy), 5, color, cv2.FILLED)

        return landmark_list


def main():
    """Demo function to test the HandDetector class."""
    import time
    
    previous_time = 0
    cap = cv2.VideoCapture(0)
    detector = HandDetector(max_hands=1)
    
    while True:
        success, img = cap.read()
        if not success:
            break
            
        img = detector.find_hands(img)
        landmark_list = detector.find_position(img, include_z=True, draw=False)
        
        if landmark_list:
            print(landmark_list[4])  # Print thumb tip position

        current_time = time.time()
        fps = 1 / (current_time - previous_time)
        previous_time = current_time

        cv2.putText(img, str(int(fps)), (10, 70), cv2.FONT_HERSHEY_PLAIN, 3,
                    (255, 0, 255), 3)

        cv2.imshow("Hand Tracking", img)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()