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
    candidates = build_qr_candidate_images(cv2, image)
    detections = []
    seen = set()
    for candidate, base_score in candidates:
        try:
            ok, decoded_info, points, _ = detector.detectAndDecodeMulti(candidate)
            if ok:
                for text in decoded_info:
                    if text and text not in seen:
                        seen.add(text)
                        detections.append(QRDetection(text=text, score=base_score + 20))
        except Exception:
            pass

        try:
            text, points, _ = detector.detectAndDecode(candidate)
        except Exception:
            continue
        if text and text not in seen:
            score = base_score
            if points is not None:
                score += 10
            seen.add(text)
            detections.append(QRDetection(text=text, score=score))
    return detections


def build_qr_candidate_images(cv2, image):
    candidates = [(image, 100)]
    height, width = image.shape[:2]

    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        candidates.append((gray, 105))
        equalized = cv2.equalizeHist(gray)
        candidates.append((equalized, 108))
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        candidates.append((clahe.apply(gray), 110))
    except Exception:
        gray = None

    for scale, score in ((1.5, 112), (2.0, 115), (3.0, 118)):
        try:
            resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            candidates.append((resized, score))
            if gray is not None:
                resized_gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                candidates.append((resized_gray, score + 2))
        except Exception:
            pass

    crop_boxes = [
        (0, 0, width, height),
        (width // 2, 0, width, height),
        (0, height // 2, width, height),
        (width // 2, height // 2, width, height),
        (width // 3, height // 3, width, height),
    ]
    for left, top, right, bottom in crop_boxes:
        if right - left < 80 or bottom - top < 80:
            continue
        crop = image[top:bottom, left:right]
        if crop.size:
            candidates.append((crop, 106))
            try:
                crop2 = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
                candidates.append((crop2, 116))
            except Exception:
                pass

    return candidates
