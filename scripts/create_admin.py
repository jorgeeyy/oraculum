"""Create (or find) an admin account from the command line.

Usage:
    uv run python scripts/create_admin.py you@example.com

The password is prompted with getpass (never echoed, never argv). When the
email already exists it is promoted to admin instead of recreated. The
seeded admin adopts pre-existing unowned conversations (the local scope,
plus the API-key scope when API_KEY is configured) and shared-corpus
documents (NULL owner), so chat history and uploads made before accounts
existed belong to them when they log in.

Registration never creates admins — this script is the only way.
"""

import getpass
import sys
import uuid

from app.api.auth import api_key_fingerprint
from app.config import get_settings
from app.db import repositories
from app.db.session import get_engine
from app.services.passwords import hash_password
from sqlalchemy.orm import sessionmaker


def main() -> int:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        print(__doc__)
        return 2
    email = sys.argv[1].strip().casefold()

    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        user = repositories.get_user_by_email(session, email)
        if user is not None:
            if user.role != "admin":
                user.role = "admin"
                session.commit()
                print(f"Promoted existing user {email} to admin.")
            else:
                print(f"User {email} is already an admin.")
        else:
            password = getpass.getpass(f"Password for {email}: ")
            if len(password) < 8:
                print("Password must be at least 8 characters.")
                return 1
            user_id = uuid.UUID(
                repositories.create_user(
                    session,
                    email=email,
                    password_hash=hash_password(password),
                    display_name=None,
                    role="admin",
                )
            )
            user = repositories.get_user(session, user_id)
            assert user is not None  # just created
            print(f"Created admin {email}.")
        # Adopt unowned conversations: the pre-identity local scope always,
        # plus the API-key scope when a key is configured.
        scopes: list[str | None] = [None]
        fingerprint = api_key_fingerprint(get_settings())
        if fingerprint is not None:
            scopes.append(fingerprint)
        adopted_total = sum(
            repositories.adopt_conversations(
                session, api_key_hash=scope, user_id=user.id
            )
            for scope in scopes
        )
        if adopted_total:
            print(f"Adopted {adopted_total} existing conversation(s) into {email}.")
        # Pre-identity uploads live in the shared corpus (NULL owner) and
        # would otherwise stay visible to every account; hand them to the
        # seeded admin so they become the admin's private documents.
        adopted_docs = repositories.adopt_documents(session, user_id=user.id)
        if adopted_docs:
            print(
                f"Adopted {adopted_docs} shared-corpus document(s) into {email}."
            )
        print("Log in with: POST /auth/login")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
