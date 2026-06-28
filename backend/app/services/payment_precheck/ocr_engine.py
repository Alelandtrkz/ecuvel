from __future__ import annotations

from decimal import Decimal

import pytesseract
from pytesseract import Output

from app.services.payment_precheck.types import OCRResult, PreparedImageSet


def run_ocr(
    *,
    prepared_images: PreparedImageSet,
    languages: str,
    timeout_seconds: int,
    min_word_confidence: int,
) -> OCRResult:
    candidates = [prepared_images.variants[0], prepared_images.variants[-1]][:2]
    results: list[tuple[int, Decimal, str]] = []
    timed_out = False
    attempts = 0
    for index, variant in enumerate(candidates):
        attempts += 1
        try:
            data = pytesseract.image_to_data(
                variant.image,
                lang=languages,
                config=f"--oem 1 --psm {6 if index == 0 else 11}",
                output_type=Output.DICT,
                timeout=timeout_seconds,
            )
        except RuntimeError:
            timed_out = True
            continue
        except pytesseract.TesseractError:
            continue
        words: list[str] = []
        lines: dict[tuple[int, int, int], list[str]] = {}
        confidences: list[Decimal] = []
        texts = data.get("text", [])
        block_numbers = data.get("block_num", [0] * len(texts))
        paragraph_numbers = data.get("par_num", [0] * len(texts))
        line_numbers = data.get("line_num", [0] * len(texts))
        for text, confidence, block, paragraph, line in zip(
            texts,
            data.get("conf", []),
            block_numbers,
            paragraph_numbers,
            line_numbers,
        ):
            token = " ".join(str(text).split())
            try:
                numeric_confidence = Decimal(str(confidence))
            except Exception:
                continue
            if token and numeric_confidence >= min_word_confidence:
                words.append(token)
                confidences.append(numeric_confidence)
                lines.setdefault(
                    (int(block), int(paragraph), int(line)), []
                ).append(token)
        if words:
            mean = sum(confidences, Decimal("0")) / len(confidences)
            reconstructed = "\n".join(
                " ".join(tokens) for tokens in lines.values()
            )
            results.append((len(words), mean, reconstructed))
    if not results:
        return OCRResult(
            text="",
            mean_confidence=None,
            word_count=0,
            timed_out=timed_out,
            attempt_count=attempts,
            error_code="OCR_TIMEOUT" if timed_out else "OCR_NO_TEXT",
        )
    word_count, confidence, text = max(results, key=lambda item: (item[0], item[1]))
    return OCRResult(
        text=text,
        mean_confidence=confidence.quantize(Decimal("0.01")),
        word_count=word_count,
        timed_out=timed_out,
        attempt_count=attempts,
        error_code="OCR_PARTIAL_TIMEOUT" if timed_out else None,
    )
