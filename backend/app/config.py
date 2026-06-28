import os


def derive_bank_account_last4(
    account_number: str | None,
    legacy_last4: str | None = None,
) -> str | None:
    """Validate bank-account configuration and expose only its suffix."""
    digits = "".join(char for char in (account_number or "") if char.isdigit())
    legacy_digits = "".join(
        char for char in (legacy_last4 or "") if char.isdigit()
    )
    if not digits:
        if legacy_digits:
            raise RuntimeError(
                "BANK_TRANSFER_ACCOUNT_LAST4 ya no puede configurarse sin "
                "BANK_TRANSFER_ACCOUNT_NUMBER."
            )
        return None
    if not 6 <= len(digits) <= 34:
        raise RuntimeError(
            "BANK_TRANSFER_ACCOUNT_NUMBER debe contener entre 6 y 34 dígitos."
        )
    derived = digits[-4:]
    if legacy_digits and (len(legacy_digits) != 4 or legacy_digits != derived):
        raise RuntimeError(
            "BANK_TRANSFER_ACCOUNT_LAST4 contradice la terminación derivada "
            "de BANK_TRANSFER_ACCOUNT_NUMBER."
        )
    return derived


def _environment_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _environment_int_range(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            f"{name} debe ser un entero entre {minimum} y {maximum}."
        ) from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} debe estar entre {minimum} y {maximum}."
        )
    return value


def _environment_choice(name: str, default: str, choices: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in choices:
        raise RuntimeError(
            f"{name} debe ser uno de: {', '.join(sorted(choices))}."
        )
    return value


class Config:
    SECRET_KEY = os.environ["SECRET_KEY"]

    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = _environment_bool("TESTING", False)
    ECUVEL_PRODUCTION = _environment_bool("ECUVEL_PRODUCTION", False)
    WTF_CSRF_ENABLED = not TESTING
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _environment_bool(
        "SESSION_COOKIE_SECURE",
        ECUVEL_PRODUCTION,
    )
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = _environment_bool(
        "REMEMBER_COOKIE_SECURE",
        ECUVEL_PRODUCTION,
    )
    PERMANENT_SESSION_LIFETIME = _environment_int_range(
        "PERMANENT_SESSION_LIFETIME_SECONDS",
        60 * 60 * 24 * 14,
        300,
        60 * 60 * 24 * 90,
    )
    REMEMBER_COOKIE_DURATION = PERMANENT_SESSION_LIFETIME
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_ENABLED = not TESTING

    AUTH_REQUIRE_EMAIL_VERIFICATION = _environment_bool(
        "AUTH_REQUIRE_EMAIL_VERIFICATION",
        True,
    )
    ALLOW_DEMO_CHECKOUT = _environment_bool(
        "ALLOW_DEMO_CHECKOUT",
        False,
    )
    EMAIL_VERIFICATION_TOKEN_TTL_MINUTES = _environment_int_range(
        "EMAIL_VERIFICATION_TOKEN_TTL_MINUTES",
        30,
        1,
        1440,
    )
    PASSWORD_RESET_TOKEN_TTL_MINUTES = _environment_int_range(
        "PASSWORD_RESET_TOKEN_TTL_MINUTES",
        30,
        1,
        1440,
    )
    AUTH_PASSWORD_MIN_LENGTH = _environment_int_range(
        "AUTH_PASSWORD_MIN_LENGTH",
        12,
        8,
        128,
    )
    MAIL_BACKEND = os.getenv("MAIL_BACKEND", "console")
    MAIL_FROM = os.getenv("MAIL_FROM", "")

    PHONE_OTP_ENABLED = _environment_bool("PHONE_OTP_ENABLED", True)
    PHONE_OTP_BACKEND = _environment_choice(
        "PHONE_OTP_BACKEND",
        "console",
        {"console", "fake"},
    )
    PHONE_OTP_TTL_SECONDS = _environment_int_range(
        "PHONE_OTP_TTL_SECONDS",
        300,
        30,
        3600,
    )
    PHONE_OTP_RESEND_COOLDOWN_SECONDS = _environment_int_range(
        "PHONE_OTP_RESEND_COOLDOWN_SECONDS",
        60,
        10,
        900,
    )
    PHONE_OTP_MAX_ATTEMPTS = _environment_int_range(
        "PHONE_OTP_MAX_ATTEMPTS",
        5,
        1,
        20,
    )
    PHONE_OTP_CODE_LENGTH = _environment_int_range(
        "PHONE_OTP_CODE_LENGTH",
        6,
        4,
        8,
    )
    PHONE_OTP_PEPPER = os.getenv(
        "PHONE_OTP_PEPPER",
        "test-only-phone-otp-pepper" if TESTING else "",
    )
    if PHONE_OTP_ENABLED and not TESTING and not PHONE_OTP_PEPPER:
        raise RuntimeError(
            "PHONE_OTP_PEPPER debe configurarse cuando PHONE_OTP_ENABLED=true."
        )
    if (
        PHONE_OTP_BACKEND == "console"
        and ECUVEL_PRODUCTION
    ):
        raise RuntimeError(
            "PHONE_OTP_BACKEND=console no estÃ¡ permitido en producciÃ³n."
        )
    PHONE_OTP_REQUEST_RATE_LIMIT = os.getenv(
        "PHONE_OTP_REQUEST_RATE_LIMIT",
        "5 per 15 minutes",
    )
    PHONE_OTP_VERIFY_RATE_LIMIT = os.getenv(
        "PHONE_OTP_VERIFY_RATE_LIMIT",
        "10 per 15 minutes",
    )
    PHONE_OTP_RESEND_RATE_LIMIT = os.getenv(
        "PHONE_OTP_RESEND_RATE_LIMIT",
        "5 per 15 minutes",
    )

    BANK_TRANSFER_PAYMENT_TIMEOUT_MINUTES = _environment_int_range(
        "BANK_TRANSFER_PAYMENT_TIMEOUT_MINUTES",
        20,
        1,
        1440,
    )
    CUSTOMER_ORDERS_PAGE_SIZE = _environment_int_range(
        "CUSTOMER_ORDERS_PAGE_SIZE",
        10,
        1,
        50,
    )
    FAVORITES_PAGE_SIZE = _environment_int_range(
        "FAVORITES_PAGE_SIZE",
        16,
        1,
        100,
    )
    PRODUCT_REVIEWS_PAGE_SIZE = _environment_int_range(
        "PRODUCT_REVIEWS_PAGE_SIZE",
        10,
        1,
        50,
    )
    PRODUCT_REVIEW_MIN_BODY_LENGTH = _environment_int_range(
        "PRODUCT_REVIEW_MIN_BODY_LENGTH",
        10,
        1,
        2000,
    )
    PRODUCT_REVIEW_MAX_BODY_LENGTH = _environment_int_range(
        "PRODUCT_REVIEW_MAX_BODY_LENGTH",
        2000,
        PRODUCT_REVIEW_MIN_BODY_LENGTH,
        2000,
    )
    PRODUCT_REVIEW_MAX_IMAGES = _environment_int_range(
        "PRODUCT_REVIEW_MAX_IMAGES",
        5,
        0,
        5,
    )
    PRODUCT_REVIEW_IMAGE_MAX_BYTES = _environment_int_range(
        "PRODUCT_REVIEW_IMAGE_MAX_BYTES",
        5 * 1024 * 1024,
        1024,
        10 * 1024 * 1024,
    )
    PRODUCT_REVIEW_IMAGES_TOTAL_MAX_BYTES = _environment_int_range(
        "PRODUCT_REVIEW_IMAGES_TOTAL_MAX_BYTES",
        20 * 1024 * 1024,
        1024,
        50 * 1024 * 1024,
    )
    PRODUCT_REVIEW_IMAGE_MAX_PIXELS = _environment_int_range(
        "PRODUCT_REVIEW_IMAGE_MAX_PIXELS",
        20_000_000,
        1_000,
        40_000_000,
    )
    PRODUCT_REVIEW_IMAGE_MAX_DIMENSION = _environment_int_range(
        "PRODUCT_REVIEW_IMAGE_MAX_DIMENSION",
        4096,
        100,
        10000,
    )
    PRODUCT_REVIEW_UPLOAD_DIR = os.getenv(
        "PRODUCT_REVIEW_UPLOAD_DIR",
        "/app/private/product-reviews",
    )
    PARTNER_DOCUMENT_UPLOAD_DIR = os.getenv(
        "PARTNER_DOCUMENT_UPLOAD_DIR",
        "/app/private/partner-documents",
    )
    PARTNER_DOCUMENT_MAX_BYTES = _environment_int_range(
        "PARTNER_DOCUMENT_MAX_BYTES",
        32 * 1024 * 1024,
        1024,
        64 * 1024 * 1024,
    )
    PARTNER_CONTRACT_UPLOAD_DIR = os.getenv(
        "PARTNER_CONTRACT_UPLOAD_DIR",
        "/app/private/partner-contracts",
    )
    PARTNER_CONTRACT_VERSION = os.getenv("PARTNER_CONTRACT_VERSION", "2026-06")
    PARTNER_CONTRACT_ANNEX_VERSION = os.getenv(
        "PARTNER_CONTRACT_ANNEX_VERSION",
        "2026-06",
    )
    PARTNER_CONTRACT_OTP_TTL_SECONDS = _environment_int_range(
        "PARTNER_CONTRACT_OTP_TTL_SECONDS",
        300,
        30,
        3600,
    )
    PARTNER_CONTRACT_OTP_RESEND_COOLDOWN_SECONDS = _environment_int_range(
        "PARTNER_CONTRACT_OTP_RESEND_COOLDOWN_SECONDS",
        60,
        10,
        900,
    )
    PARTNER_CONTRACT_OTP_MAX_ATTEMPTS = _environment_int_range(
        "PARTNER_CONTRACT_OTP_MAX_ATTEMPTS",
        5,
        1,
        20,
    )
    PARTNER_CONTRACT_OTP_RATE_LIMIT = os.getenv(
        "PARTNER_CONTRACT_OTP_RATE_LIMIT",
        "5 per 15 minutes",
    )
    CHECKOUT_RESERVATION_MINUTES = _environment_int_range(
        "CHECKOUT_RESERVATION_MINUTES",
        BANK_TRANSFER_PAYMENT_TIMEOUT_MINUTES,
        1,
        1440,
    )
    CHECKOUT_DEMO_BUYER_EMAIL = os.getenv(
        "CHECKOUT_DEMO_BUYER_EMAIL", "buyer@ecuvel.local"
    )
    ECUVEL_PICKUP_POINT_NAME = os.getenv(
        "ECUVEL_PICKUP_POINT_NAME", "Punto de entrega Ecuvel"
    )
    ECUVEL_PICKUP_POINT_ADDRESS = os.getenv(
        "ECUVEL_PICKUP_POINT_ADDRESS",
        "Dirección pendiente de configuración",
    )
    ECUVEL_ORDER_HOLD_DAYS = int(
        os.getenv("ECUVEL_ORDER_HOLD_DAYS", "14")
    )
    ECUVEL_PICKUP_IS_FREE = _environment_bool(
        "ECUVEL_PICKUP_IS_FREE", True
    )

    BANK_TRANSFER_BANK_NAME = os.getenv("BANK_TRANSFER_BANK_NAME")
    BANK_TRANSFER_ACCOUNT_HOLDER = os.getenv(
        "BANK_TRANSFER_ACCOUNT_HOLDER"
    )
    BANK_TRANSFER_ACCOUNT_NUMBER = os.getenv(
        "BANK_TRANSFER_ACCOUNT_NUMBER"
    )
    BANK_TRANSFER_HOLDER_ID = os.getenv("BANK_TRANSFER_HOLDER_ID")
    BANK_TRANSFER_EMAIL = os.getenv("BANK_TRANSFER_EMAIL")
    BANK_TRANSFER_QR_IMAGE = os.getenv("BANK_TRANSFER_QR_IMAGE")

    PAYMENT_PROOF_UPLOAD_DIR = os.getenv(
        "PAYMENT_PROOF_UPLOAD_DIR", "/app/private/payment-proofs"
    )
    PAYMENT_PROOF_MAX_BYTES = int(
        os.getenv("PAYMENT_PROOF_MAX_BYTES", "10485760")
    )
    PAYMENT_PROOF_ALLOWED_EXTENSIONS = tuple(
        item.strip().lower()
        for item in os.getenv(
            "PAYMENT_PROOF_ALLOWED_EXTENSIONS", "jpg,jpeg,png,pdf"
        ).split(",")
        if item.strip()
    )
    PAYMENT_PROOF_ALLOWED_MEDIA_TYPES = tuple(
        item.strip().lower()
        for item in os.getenv(
            "PAYMENT_PROOF_ALLOWED_MEDIA_TYPES",
            "image/jpeg,image/png,application/pdf",
        ).split(",")
        if item.strip()
    )
    MAX_CONTENT_LENGTH = PAYMENT_PROOF_MAX_BYTES + 1048576

    PAYMENT_PRECHECK_ENABLED = _environment_bool(
        "PAYMENT_PRECHECK_ENABLED", True
    )
    PAYMENT_PRECHECK_ANALYZER_VERSION = os.getenv(
        "PAYMENT_PRECHECK_ANALYZER_VERSION", "2"
    )
    PAYMENT_PRECHECK_TESSERACT_LANG = os.getenv(
        "PAYMENT_PRECHECK_TESSERACT_LANG", "spa+eng"
    )
    PAYMENT_PRECHECK_TESSERACT_TIMEOUT_SECONDS = int(
        os.getenv("PAYMENT_PRECHECK_TESSERACT_TIMEOUT_SECONDS", "10")
    )
    PAYMENT_PRECHECK_MAX_IMAGE_PIXELS = int(
        os.getenv("PAYMENT_PRECHECK_MAX_IMAGE_PIXELS", "20000000")
    )
    PAYMENT_PRECHECK_MAX_DIMENSION = int(
        os.getenv("PAYMENT_PRECHECK_MAX_DIMENSION", "4096")
    )
    PAYMENT_PRECHECK_OCR_MIN_CONFIDENCE = int(
        os.getenv("PAYMENT_PRECHECK_OCR_MIN_CONFIDENCE", "30")
    )
    PAYMENT_PRECHECK_LOW_CONFIDENCE_THRESHOLD = int(
        os.getenv("PAYMENT_PRECHECK_LOW_CONFIDENCE_THRESHOLD", "60")
    )
    PAYMENT_PRECHECK_MAX_AGE_DAYS = int(
        os.getenv("PAYMENT_PRECHECK_MAX_AGE_DAYS", "7")
    )
    PAYMENT_PRECHECK_FUTURE_TOLERANCE_MINUTES = int(
        os.getenv("PAYMENT_PRECHECK_FUTURE_TOLERANCE_MINUTES", "10")
    )
    PAYMENT_PRECHECK_TIMEZONE = os.getenv(
        "PAYMENT_PRECHECK_TIMEZONE", "America/Guayaquil"
    )
    PAYMENT_PRECHECK_MAX_QR_PAYLOAD_CHARS = int(
        os.getenv("PAYMENT_PRECHECK_MAX_QR_PAYLOAD_CHARS", "4096")
    )
    PAYMENT_PRECHECK_STALE_SECONDS = int(
        os.getenv("PAYMENT_PRECHECK_STALE_SECONDS", "120")
    )
    BANK_TRANSFER_ACCOUNT_LAST4 = derive_bank_account_last4(
        BANK_TRANSFER_ACCOUNT_NUMBER,
        os.getenv("BANK_TRANSFER_ACCOUNT_LAST4"),
    )
    BANK_TRANSFER_ALLOWED_BANKS = tuple(
        item.strip()
        for item in os.getenv("BANK_TRANSFER_ALLOWED_BANKS", "").split(",")
        if item.strip()
    )

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_recycle": 1800,
    }
