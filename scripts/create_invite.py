import asyncio
import asyncpg
import secrets
import argparse
from config.settings import settings
from services.invites import build_invite_link
from services.notifier import EmailDeliveryError, send_invite_email


async def create_invite(email: str, org_name: str, should_send_email: bool):
    email = email.strip().lower()
    org_name = org_name.strip()
    conn = await asyncpg.connect(settings.our_db_url)
    created_invite = False

    try:
        registered_client = await conn.fetchrow(
            """
            SELECT clients.email, organizations.id AS org_id, organizations.name AS org_name
            FROM clients
            JOIN organizations ON organizations.id = clients.org_id
            WHERE LOWER(clients.email) = LOWER($1)
            LIMIT 1
            """,
            email
        )
        if registered_client:
            print(f"\nUser already registered: {registered_client['email']}")
            print(f"Organization: {registered_client['org_name']} (id: {registered_client['org_id']})")
            return

        existing_invite = await conn.fetchrow(
            """
            SELECT invites.token, organizations.id AS org_id, organizations.name AS org_name
            FROM invites
            JOIN organizations ON organizations.id = invites.org_id
            WHERE LOWER(invites.email) = LOWER($1) AND COALESCE(invites.used, FALSE) = FALSE
            ORDER BY invites.created_at DESC
            LIMIT 1
            """,
            email
        )

        if existing_invite:
            token = existing_invite["token"]
            org = {
                "id": existing_invite["org_id"],
                "name": existing_invite["org_name"],
            }
        else:
            token = secrets.token_hex(16)

            org = await conn.fetchrow(
                """
                SELECT id, name
                FROM organizations
                WHERE LOWER(name) = LOWER($1) AND is_active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
                """,
                org_name
            )
            if not org:
                org = await conn.fetchrow(
                    "INSERT INTO organizations (name) VALUES ($1) RETURNING id, name",
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
            created_invite = True
    finally:
        await conn.close()

    invite_link = build_invite_link(token, email)

    if created_invite:
        print(f"\nOrg created:  {org['name']} (id: {org['id']})")
    else:
        print(f"\nExisting pending invite found for: {email}")
        print(f"Organization: {org['name']} (id: {org['id']})")

    print(f"Invite email: {email}")
    print(f"\nInvite link:")
    print(invite_link)

    if should_send_email:
        try:
            result = await asyncio.to_thread(
                send_invite_email,
                email,
                invite_link,
                org["name"],
                "Saaspro Lab"
            )
            print(f"\nInvite email sent. Resend id: {result.get('id')}")
        except EmailDeliveryError as exc:
            print(f"\nInvite created, but email was not sent: {exc}")

parser = argparse.ArgumentParser()
parser.add_argument("--email", required=True)
parser.add_argument("--org", required=True, help="Organization name e.g. 'Aman HMO'")
parser.add_argument("--no-email", action="store_true", help="Create the invite without sending email")
args = parser.parse_args()

asyncio.run(create_invite(args.email, args.org, not args.no_email))
