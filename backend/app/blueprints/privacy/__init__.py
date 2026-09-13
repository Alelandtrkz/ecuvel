from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from flask_login import current_user

from app.extensions import db
from app.models.enums import PrivacyPreferenceDecision, PrivacyPreferenceSource
from app.services.privacy_preferences import (
    PrivacyPreferenceError,
    PrivacyNoticeUnavailableError,
    StalePrivacyNoticeError,
    record_privacy_preference,
    resolve_privacy_preference_state,
)
from app.services.safe_redirects import safe_local_redirect


privacy = Blueprint("privacy", __name__, url_prefix="/privacidad")


def _current_user_id():
    return current_user.id if current_user.is_authenticated else None


@privacy.get("/preferencias")
def preferences():
    try:
        state = resolve_privacy_preference_state(
            db.session,
            flask_session,
            user_id=_current_user_id(),
        )
    except PrivacyPreferenceError:
        return render_template("legal/unavailable.html"), 503
    return render_template(
        "privacy/preferences.html",
        preference_state=state,
    )


@privacy.post("/preferencias")
def update_preferences():
    return_to = safe_local_redirect(
        request.form.get("return_to"),
        fallback=url_for("privacy.preferences"),
    )
    user_id = _current_user_id()
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            record_privacy_preference(
                database_session,
                flask_session,
                user_id=user_id,
                decision=request.form.get("decision", ""),
                source=PrivacyPreferenceSource.PRIVACY_SETTINGS,
                presented_notice_identifier=(
                    request.form.get("notice_version_presented") or None
                ),
            )
    except PrivacyNoticeUnavailableError:
        flash(
            "La telemetría opcional seguirá desactivada hasta que su aviso "
            "esté publicado.",
            "warning",
        )
        return redirect(url_for("privacy.preferences"))
    except StalePrivacyNoticeError:
        flash(
            "El aviso aplicable cambió. Revísalo antes de volver a elegir.",
            "warning",
        )
        return redirect(url_for("privacy.preferences"))
    except PrivacyPreferenceError:
        flash("No pudimos guardar esa preferencia.", "error")
        return redirect(url_for("privacy.preferences"))

    if request.form.get("decision") == PrivacyPreferenceDecision.GRANTED.value:
        flash("Telemetría opcional activada para este contexto.", "success")
    else:
        flash(
            "Telemetría opcional desactivada. Puedes seguir usando ECUVEL normalmente.",
            "success",
        )
    return redirect(return_to)
