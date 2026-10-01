"""`sales-agent secrets ...`: the master key that opens the credential store.

    generate-key    print a new random master key (it is not stored anywhere)

Put the printed value in the SALES_AGENT_SECRETS_KEY environment variable of the deployment, and
keep a copy somewhere safe and separate from the backups of the secrets directory: without it the
stored credentials cannot be read, and with it anyone holding those backups can read them.
"""

import argparse

from sales_app.secret_store import generate_master_key

EXIT_OK = 0


def add_secrets_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    secrets = commands.add_parser("secrets", help="manage the credential store's master key")
    actions = secrets.add_subparsers(dest="secrets_command", required=True)
    actions.add_parser("generate-key", help="print a new random master key")


def run_secrets(args: argparse.Namespace) -> int:
    print(generate_master_key())
    return EXIT_OK
