from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.models import User
from app.models.enums import UserStatus
from app.services.user_profiles import ProfileError, update_profile


pytestmark = pytest.mark.integration

TODAY = date(2026, 9, 4)


def _user(session, *, birth_date: date | None) -> User:
    token = uuid.uuid4().hex[:12]
    user = User(
        public_code=f"ECV-U-{token.upper()}",
        email=f"profile-{token}@test.local",
        full_name="Nombre Original",
        birth_date=birth_date,
        gender="female",
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _update(
    session,
    user: User,
    *,
    birth_date: date | None,
    full_name: str = "Nombre Actualizado",
    gender: str = "male",
) -> User:
    return update_profile(
        session=session,
        user_id=user.id,
        full_name=full_name,
        phone=None,
        birth_date=birth_date,
        gender=gender,
        today=TODAY,
    )


def test_missing_birth_date_can_remain_empty_while_other_fields_change(session):
    user = _user(session, birth_date=None)

    _update(session, user, birth_date=None)

    assert user.birth_date is None
    assert user.full_name == "Nombre Actualizado"
    assert user.gender == "male"


@pytest.mark.parametrize(
    "birth_date",
    (date(2001, 9, 4), date(2008, 9, 4)),
)
def test_missing_birth_date_accepts_adult_date(session, birth_date):
    user = _user(session, birth_date=None)

    _update(session, user, birth_date=birth_date)

    assert user.birth_date == birth_date


def test_missing_birth_date_rejects_birthday_before_adult_boundary(session):
    user = _user(session, birth_date=None)

    with pytest.raises(ProfileError, match="al menos 18 años"):
        _update(session, user, birth_date=date(2008, 9, 5))

    assert user.birth_date is None
    assert user.full_name == "Nombre Original"
    assert user.gender == "female"


def test_missing_birth_date_rejects_future_date(session):
    user = _user(session, birth_date=None)

    with pytest.raises(ProfileError, match="futuro"):
        _update(session, user, birth_date=date(2026, 9, 5))

    assert user.birth_date is None


def test_same_valid_birth_date_allows_other_profile_changes(session):
    birth_date = date(2000, 1, 1)
    user = _user(session, birth_date=birth_date)

    _update(session, user, birth_date=birth_date)

    assert user.birth_date == birth_date
    assert user.full_name == "Nombre Actualizado"
    assert user.gender == "male"


def test_valid_birth_date_is_immutable_and_update_is_atomic(session):
    original_birth_date = date(2000, 1, 1)
    user = _user(session, birth_date=original_birth_date)
    user_id = user.id
    session.commit()

    with pytest.raises(ProfileError, match="no puede modificarse"):
        _update(
            session,
            user,
            birth_date=date(1999, 1, 1),
            full_name="Nombre Que No Debe Guardarse",
        )
    session.rollback()
    session.expire_all()

    stored = session.get(User, user_id)
    assert stored.birth_date == original_birth_date
    assert stored.full_name == "Nombre Original"
    assert stored.gender == "female"


def test_valid_birth_date_cannot_be_deleted(session):
    original_birth_date = date(2000, 1, 1)
    user = _user(session, birth_date=original_birth_date)
    user_id = user.id
    session.commit()

    with pytest.raises(ProfileError, match="no puede eliminarse"):
        _update(session, user, birth_date=None)
    session.rollback()
    session.expire_all()

    assert session.get(User, user_id).birth_date == original_birth_date


def test_legacy_birth_date_can_be_corrected_to_adult_date(session):
    user = _user(session, birth_date=date(2010, 1, 1))

    _update(session, user, birth_date=date(2000, 1, 1))

    assert user.birth_date == date(2000, 1, 1)


def test_legacy_birth_date_cannot_change_to_another_invalid_date(session):
    original_birth_date = date(2010, 1, 1)
    user = _user(session, birth_date=original_birth_date)
    user_id = user.id
    session.commit()

    with pytest.raises(ProfileError, match="al menos 18 años"):
        _update(session, user, birth_date=date(2011, 1, 1))
    session.rollback()
    session.expire_all()

    assert session.get(User, user_id).birth_date == original_birth_date


def test_same_legacy_birth_date_allows_other_profile_changes(session):
    legacy_birth_date = date(2010, 1, 1)
    user = _user(session, birth_date=legacy_birth_date)

    _update(session, user, birth_date=legacy_birth_date)

    assert user.birth_date == legacy_birth_date
    assert user.full_name == "Nombre Actualizado"
    assert user.gender == "male"


def test_corrected_legacy_birth_date_becomes_immutable(session):
    user = _user(session, birth_date=date(2010, 1, 1))

    _update(session, user, birth_date=date(2000, 1, 1))
    session.commit()

    with pytest.raises(ProfileError, match="no puede modificarse"):
        _update(session, user, birth_date=date(1999, 1, 1))
    session.rollback()
    session.expire_all()

    assert session.get(User, user.id).birth_date == date(2000, 1, 1)
