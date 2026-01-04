#!/usr/bin/env python3
"""Simple admin CLI to manage payment requirement and paid users.

Usage examples:
  python admin_payment.py status
  python admin_payment.py enable
  python admin_payment.py disable
  python admin_payment.py mark-paid 12345678
  python admin_payment.py mark-unpaid 12345678
  python admin_payment.py list-paid
"""
import argparse
from payment_gate import (
    set_payment_required, is_payment_required,
    mark_user_paid, mark_user_unpaid, list_paid_users, show_settings
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['status', 'enable', 'disable', 'mark-paid', 'mark-unpaid', 'list-paid'])
    p.add_argument('user', nargs='?')
    args = p.parse_args()

    cmd = args.command
    if cmd == 'status':
        print('payment_required=', is_payment_required())
        print('settings=', show_settings())
    elif cmd == 'enable':
        set_payment_required(True)
        print('payment requirement enabled')
    elif cmd == 'disable':
        set_payment_required(False)
        print('payment requirement disabled')
    elif cmd == 'mark-paid':
        if not args.user:
            print('user id required')
            return
        mark_user_paid(args.user)
        print('marked paid:', args.user)
    elif cmd == 'mark-unpaid':
        if not args.user:
            print('user id required')
            return
        mark_user_unpaid(args.user)
        print('marked unpaid:', args.user)
    elif cmd == 'list-paid':
        users = list_paid_users()
        print('\n'.join(users) if users else '(no paid users)')


if __name__ == '__main__':
    main()
