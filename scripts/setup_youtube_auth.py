#!/usr/bin/env python3
"""
scripts/setup_youtube_auth.py
Run once to complete the YouTube OAuth flow and save a token file.

Usage:
    python scripts/setup_youtube_auth.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials
    import pickle
except ImportError:
    print("❌ Missing dependencies. Run: pip install google-auth-oauthlib google-auth")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    secret_file = settings.youtube_client_secret_file
    token_file = settings.youtube_token_file

    if not secret_file.exists():
        print(f"❌ Client secret not found: {secret_file}")
        print("   Download it from Google Cloud Console → APIs & Services → Credentials")
        sys.exit(1)

    token_file.parent.mkdir(parents=True, exist_ok=True)

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), SCOPES)
    creds = flow.run_local_server(port=0)

    with open(token_file, "wb") as f:
        pickle.dump(creds, f)

    print(f"✅ Token saved to {token_file}")
    print("   YouTube OAuth setup complete!")


if __name__ == "__main__":
    main()
