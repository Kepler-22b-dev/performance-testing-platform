# Mock Ecommerce Pressure Test

## Assets

- Mock API server: `mock-server.py`
- JMeter script: `scripts/ecommerce-mock-full-flow-10m.jmx`
- User CSV: `scripts/ecommerce-mock-users.csv`
- Latest run summary: `reports/mock-ecommerce-10m-distributed/run-summary.md`

## Flow

1. Register user
2. Login and extract token
3. Search products and extract `product_id`
4. Add product to cart and extract `cart_item_id`
5. Create order and extract `order_id`
6. View order detail
7. Cancel order

## Run Locally

Start the mock API:

```bash
MOCK_PORT=9100 MOCK_ECOMMERCE_TRACE=mock-data/ecommerce-trace-10m-distributed.jsonl python3 mock-server.py
```

Start two local JMeter slaves:

```bash
apache-jmeter-5.6.3-slave/bin/jmeter-server
apache-jmeter-5.6.3-slave2/bin/jmeter-server
```

Run the distributed pressure test:

```bash
apache-jmeter-5.6.3/bin/jmeter \
  -n \
  -t scripts/ecommerce-mock-full-flow-10m.jmx \
  -R 127.0.0.1:1100,127.0.0.1:1200 \
  -l reports/mock-ecommerce-10m-distributed/agent-distributed/result.jtl \
  -j reports/mock-ecommerce-10m-distributed/agent-distributed/jmeter.log \
  -Jjmeter.save.saveservice.output_format=csv \
  -Jjmeter.save.saveservice.print_field_names=true
```

Generate HTML dashboard:

```bash
apache-jmeter-5.6.3/bin/jmeter \
  -g reports/mock-ecommerce-10m-distributed/agent-distributed/result.jtl \
  -o reports/mock-ecommerce-10m-distributed/agent-distributed/html-report
```

## Notes

- The script is configured as 20 threads per slave for 10 minutes.
- With two slaves, total active threads are about 40 after ramp-up.
- The CSV file must be present on each slave at the same relative path when using true remote machines.
- The mock server writes one JSONL trace per business request; the trace count should match the JTL sample count.
