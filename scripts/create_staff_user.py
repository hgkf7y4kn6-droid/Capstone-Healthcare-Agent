"""Create a staff account (run on the server, or locally against a local database).

    python -m scripts.create_staff_user --email dana@clinic.org --name "Dana Reyes" --role admin

The first admin has to be made this way; after that admins can add people in
the console API. The password is read from the terminal (never from an
argument or the environment). The authenticator secret is printed once: add
it to an authenticator app now, because it can't be shown again.
"""

import argparse
import getpass
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import staff_auth
from data import patient_db as db


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", required=True, choices=staff_auth.ROLES)
    args = parser.parse_args()
    password = getpass.getpass(f"Password (at least {staff_auth.MIN_PASSWORD} characters): ")
    if password != getpass.getpass("Again: "):
        print("Passwords didn't match.", file=sys.stderr)
        return 1
    db.init_db(seed=False)
    try:
        user = staff_auth.create_account(args.email, args.name, args.role, password)
    except ValueError as e:
        print(f"Couldn't create the account: {e}", file=sys.stderr)
        return 1
    except Exception:
        print("An account with that email already exists.", file=sys.stderr)
        return 1
    print(f"Created {user['role']} account for {user['email']}.")
    print(f"Authenticator secret (shown once): {user['totp_secret']}")
    print(f"Or open this in an authenticator app: {user['totp_uri']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
