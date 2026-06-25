from dataclasses import dataclass
from typing import List


@dataclass
class QRDetection:
    text: str
    score: int


def opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except Exception:
        return False
    return True


def detect_qr_codes(image_bytes: bytes) -> List[QRDetection]:
    try:
        import cv2
        import numpy as np
    except Exception as exc:
        raise RuntimeError("OpenCV is not available") from exc

    if not image_bytes:
        return []
    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        return []

    detector = cv2.QRCodeDetector()
    detections = []
    try:
        ok, decoded_info, points, _ = detector.detectAndDecodeMulti(image)
        if ok:
            for text in decoded_info:
                if text:
                    detections.append(QRDetection(text=text, score=100))
    except Exception:
        pass

    if detections:
        return detections

    try:
        text, points, _ = detector.detectAndDecode(image)
    except Exception:
        return []
    if text:
        score = 100
        if points is not None:
            score += 10
        detections.append(QRDetection(text=text, score=score))
    return detections
