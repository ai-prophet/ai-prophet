#!/usr/bin/env python3
"""Check current database state for orders and positions."""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
db_url = os.getenv('DATABASE_URL')
engine = create_engine(db_url)

with engine.connect() as conn:
    # Check Haifeng orders
    result = conn.execute(text('''
        SELECT COUNT(*), COALESCE(SUM(quantity * fill_price), 0) as total_spent
        FROM betting_orders
        WHERE instance_name = 'Haifeng'
        AND status = 'FILLED'
        AND dry_run = false
    ''')).fetchone()

    haifeng_orders = result[0] if result else 0
    haifeng_spent = float(result[1]) if result and result[1] else 0.0

    # Check Haifeng positions
    result = conn.execute(text('''
        SELECT COUNT(*), COALESCE(SUM(quantity * avg_price), 0) as total_deployed
        FROM trading_positions
        WHERE instance_name = 'Haifeng'
        AND quantity > 0
    ''')).fetchone()

    haifeng_positions = result[0] if result else 0
    haifeng_deployed = float(result[1]) if result and result[1] else 0.0

    print(f'Haifeng:')
    print(f'  Orders: {haifeng_orders}')
    print(f'  Total spent (from orders): ${haifeng_spent:.2f}')
    print(f'  Open positions: {haifeng_positions}')
    print(f'  Capital deployed: ${haifeng_deployed:.2f}')

    # Check Jibang too
    result = conn.execute(text('''
        SELECT COUNT(*), COALESCE(SUM(quantity * fill_price), 0) as total_spent
        FROM betting_orders
        WHERE instance_name = 'Jibang'
        AND status = 'FILLED'
        AND dry_run = false
    ''')).fetchone()

    jibang_orders = result[0] if result else 0
    jibang_spent = float(result[1]) if result and result[1] else 0.0

    result = conn.execute(text('''
        SELECT COUNT(*), COALESCE(SUM(quantity * avg_price), 0) as total_deployed
        FROM trading_positions
        WHERE instance_name = 'Jibang'
        AND quantity > 0
    ''')).fetchone()

    jibang_positions = result[0] if result else 0
    jibang_deployed = float(result[1]) if result and result[1] else 0.0

    print(f'\nJibang:')
    print(f'  Orders: {jibang_orders}')
    print(f'  Total spent (from orders): ${jibang_spent:.2f}')
    print(f'  Open positions: {jibang_positions}')
    print(f'  Capital deployed: ${jibang_deployed:.2f}')
