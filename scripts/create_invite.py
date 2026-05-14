import asyncio
import asyncpg
import secrets
import argparse
from config.settings import settings

async def create_invite(email: str, org_name: str):
    token = secrets.token_hex(16)
    conn = await asyncpg.connect(settings.our_db_url)

    # Create org
    org = await conn.fetchrow(
        "INSERT INTO organizations (name) VALUES ($1) RETURNING id",
        org_name
    )

    # Create invite as admin, no invited_by (you're inviting them)
    await conn.execute(
        """
        INSERT INTO invites (org_id, email, token, role, invited_by)
        VALUES ($1, $2, $3, 'admin', NULL)
        """,
        org["id"], email, token
    )

    await conn.close()

    print(f"\nOrg created:  {org_name} (id: {org['id']})")
    print(f"Invite email: {email}")
    print(f"\nInvite link:")
    print(f"https://your-dashboard.com/register?token={token}&email={email}")

parser = argparse.ArgumentParser()
parser.add_argument("--email", required=True)
parser.add_argument("--org", required=True, help="Organization name e.g. 'Aman HMO'")
args = parser.parse_args()

asyncio.run(create_invite(args.email, args.org))