-- Insert sample timeline data with various actions and fill statuses
-- This will create BUY, SELL, HOLD events with different fill scenarios

-- Set variables
DO $$
DECLARE
    market_id TEXT := 'TIMELINE-TEST-001';
    ticker TEXT := 'TIMELINE';
    instance TEXT := 'Haifeng';
    now_time TIMESTAMP := NOW();
BEGIN
    -- Create/update test market
    INSERT INTO trading_markets (
        market_id, ticker, event_ticker, title, subtitle, yes_subtitle, no_subtitle,
        category, rules, image_url, last_price, yes_bid, yes_ask, no_bid, no_ask,
        yes_price_24h_ago, no_price_24h_ago, volume_24h, volume, open_interest,
        expected_date, instance_name, last_traded_ts, sync_ts
    ) VALUES (
        market_id, ticker, ticker, 'Timeline Test Market',
        'Testing timeline display', 'Yes wins', 'No wins',
        'TEST', 'Test rules', NULL, 0.17, 0.15, 0.17, 0.83, 0.85,
        0.50, 0.50, 1000, 5000, 2000,
        now_time + INTERVAL '7 days', instance,
        now_time, now_time
    )
    ON CONFLICT (market_id, instance_name) DO UPDATE
    SET title = EXCLUDED.title;

    -- 1. BUY event (fully filled) - 2 hours ago
    INSERT INTO betting_predictions (
        tick_ts, market_id, instance_name, model_name,
        p_yes, yes_bid, yes_ask, no_bid, no_ask
    ) VALUES (
        now_time - INTERVAL '2 hours', market_id, instance, 'gemini-3.1-pro',
        0.68, 0.14, 0.16, 0.84, 0.86
    ) RETURNING id INTO @pred_id1;

    INSERT INTO betting_signals (
        prediction_id, instance_name, strategy_name,
        side, price, shares, cost, created_at
    ) VALUES (
        (SELECT MAX(id) FROM betting_predictions WHERE market_id = market_id),
        instance, 'default', 'yes', 16, 52, 832,
        now_time - INTERVAL '2 hours'
    ) RETURNING id INTO @signal_id1;

    INSERT INTO betting_orders (
        signal_id, market_id, instance_name, ticker, event_ticker,
        client_order_id, order_id, action, side, count, filled_shares,
        price_cents, status, created_at, updated_at, last_fill_time
    ) VALUES (
        (SELECT MAX(id) FROM betting_signals WHERE instance_name = instance),
        market_id, instance, ticker, ticker,
        'buy-001', 'kalshi-buy-001', 'buy', 'yes', 52, 52,
        16, 'FILLED', now_time - INTERVAL '2 hours',
        now_time - INTERVAL '2 hours' + INTERVAL '5 seconds',
        now_time - INTERVAL '2 hours' + INTERVAL '5 seconds'
    );

    -- 2. HOLD event - 1.5 hours ago
    INSERT INTO betting_predictions (
        tick_ts, market_id, instance_name, model_name,
        p_yes, yes_bid, yes_ask, no_bid, no_ask
    ) VALUES (
        now_time - INTERVAL '90 minutes', market_id, instance, 'gemini-3.1-pro',
        0.19, 0.16, 0.18, 0.82, 0.84
    );
    -- No signal/order for HOLD

    -- 3. BUY more (partially filled) - 1 hour ago
    INSERT INTO betting_predictions (
        tick_ts, market_id, instance_name, model_name,
        p_yes, yes_bid, yes_ask, no_bid, no_ask
    ) VALUES (
        now_time - INTERVAL '1 hour', market_id, instance, 'gemini-3.1-pro',
        0.97, 0.15, 0.17, 0.83, 0.85
    ) RETURNING id INTO @pred_id2;

    INSERT INTO betting_signals (
        prediction_id, instance_name, strategy_name,
        side, price, shares, cost, created_at
    ) VALUES (
        (SELECT MAX(id) FROM betting_predictions WHERE market_id = market_id AND tick_ts = now_time - INTERVAL '1 hour'),
        instance, 'default', 'yes', 17, 28, 476,
        now_time - INTERVAL '1 hour'
    );

    INSERT INTO betting_orders (
        signal_id, market_id, instance_name, ticker, event_ticker,
        client_order_id, order_id, action, side, count, filled_shares,
        price_cents, status, created_at, updated_at, last_fill_time, dry_run
    ) VALUES (
        (SELECT MAX(id) FROM betting_signals WHERE instance_name = instance),
        market_id, instance, ticker, ticker,
        'buy-002', 'kalshi-buy-002', 'buy', 'yes', 28, 20,
        17, 'DRY_RUN', now_time - INTERVAL '1 hour',
        now_time - INTERVAL '1 hour' + INTERVAL '10 seconds',
        now_time - INTERVAL '1 hour' + INTERVAL '10 seconds',
        true
    );

    -- 4. Position adjustment (SELL + BUY) - 30 minutes ago
    -- SELL part
    INSERT INTO betting_predictions (
        tick_ts, market_id, instance_name, model_name,
        p_yes, yes_bid, yes_ask, no_bid, no_ask
    ) VALUES (
        now_time - INTERVAL '30 minutes', market_id, instance, 'gemini-3.1-pro',
        0.10, 0.17, 0.19, 0.81, 0.83
    );

    INSERT INTO betting_signals (
        prediction_id, instance_name, strategy_name,
        side, price, shares, cost, created_at
    ) VALUES (
        (SELECT MAX(id) FROM betting_predictions WHERE market_id = market_id AND tick_ts = now_time - INTERVAL '30 minutes'),
        instance, 'default', 'yes', 18, 80, -1440,
        now_time - INTERVAL '30 minutes'
    );

    INSERT INTO betting_orders (
        signal_id, market_id, instance_name, ticker, event_ticker,
        client_order_id, order_id, action, side, count, filled_shares,
        price_cents, status, created_at, updated_at, last_fill_time
    ) VALUES (
        (SELECT MAX(id) FROM betting_signals WHERE instance_name = instance),
        market_id, instance, ticker, ticker,
        'sell-001', 'kalshi-sell-001', 'sell', 'yes', 80, 80,
        18, 'FILLED', now_time - INTERVAL '30 minutes',
        now_time - INTERVAL '30 minutes' + INTERVAL '3 seconds',
        now_time - INTERVAL '30 minutes' + INTERVAL '3 seconds'
    );

    -- BUY part (same timestamp)
    INSERT INTO betting_predictions (
        tick_ts, market_id, instance_name, model_name,
        p_yes, yes_bid, yes_ask, no_bid, no_ask
    ) VALUES (
        now_time - INTERVAL '30 minutes', market_id, instance, 'gemini-3.1-pro',
        0.10, 0.17, 0.19, 0.81, 0.83
    );

    INSERT INTO betting_signals (
        prediction_id, instance_name, strategy_name,
        side, price, shares, cost, created_at
    ) VALUES (
        (SELECT MAX(id) FROM betting_predictions WHERE market_id = market_id AND tick_ts = now_time - INTERVAL '30 minutes'),
        instance, 'default', 'no', 83, 100, 8300,
        now_time - INTERVAL '30 minutes'
    );

    INSERT INTO betting_orders (
        signal_id, market_id, instance_name, ticker, event_ticker,
        client_order_id, order_id, action, side, count, filled_shares,
        price_cents, status, created_at, updated_at, last_fill_time
    ) VALUES (
        (SELECT MAX(id) FROM betting_signals WHERE instance_name = instance),
        market_id, instance, ticker, ticker,
        'buy-003', 'kalshi-buy-003', 'buy', 'no', 100, 75,
        83, 'PENDING', now_time - INTERVAL '30 minutes',
        now_time - INTERVAL '30 minutes' + INTERVAL '5 seconds',
        now_time - INTERVAL '30 minutes' + INTERVAL '5 seconds'
    );

    -- 5. HOLD (market too extreme) - 15 minutes ago
    INSERT INTO betting_predictions (
        tick_ts, market_id, instance_name, model_name,
        p_yes, yes_bid, yes_ask, no_bid, no_ask
    ) VALUES (
        now_time - INTERVAL '15 minutes', market_id, instance, 'gemini-3.1-pro',
        0.02, 0.01, 0.02, 0.98, 0.99
    );
    -- No signal/order for HOLD (market at 2% - too extreme)

    -- 6. Recent SELL (unfilled) - 5 minutes ago
    INSERT INTO betting_predictions (
        tick_ts, market_id, instance_name, model_name,
        p_yes, yes_bid, yes_ask, no_bid, no_ask
    ) VALUES (
        now_time - INTERVAL '5 minutes', market_id, instance, 'gemini-3.1-pro',
        0.05, 0.18, 0.20, 0.80, 0.82
    );

    INSERT INTO betting_signals (
        prediction_id, instance_name, strategy_name,
        side, price, shares, cost, created_at
    ) VALUES (
        (SELECT MAX(id) FROM betting_predictions WHERE market_id = market_id AND tick_ts = now_time - INTERVAL '5 minutes'),
        instance, 'default', 'no', 80, 50, -4000,
        now_time - INTERVAL '5 minutes'
    );

    INSERT INTO betting_orders (
        signal_id, market_id, instance_name, ticker, event_ticker,
        client_order_id, order_id, action, side, count, filled_shares,
        price_cents, status, created_at, updated_at, dry_run
    ) VALUES (
        (SELECT MAX(id) FROM betting_signals WHERE instance_name = instance),
        market_id, instance, ticker, ticker,
        'sell-002', 'kalshi-sell-002', 'sell', 'no', 50, 0,
        80, 'DRY_RUN', now_time - INTERVAL '5 minutes',
        now_time - INTERVAL '5 minutes' + INTERVAL '2 seconds',
        true
    );

    RAISE NOTICE 'Created timeline test data for market: %', ticker;
    RAISE NOTICE 'Events created:';
    RAISE NOTICE '  - 2h ago: BUY 52 YES (52/52 filled) ✓';
    RAISE NOTICE '  - 1.5h ago: HOLD (edge too small)';
    RAISE NOTICE '  - 1h ago: BUY 28 YES (20/28 filled)';
    RAISE NOTICE '  - 30m ago: ADJUST - SELL 80 YES (80/80) → BUY 100 NO (75/100)';
    RAISE NOTICE '  - 15m ago: HOLD (market at 2% - too extreme)';
    RAISE NOTICE '  - 5m ago: SELL 50 NO (0/50 filled)';
END $$;