# Ecommerce Order Flow JMeter Script

## 文件

- JMeter 脚本: `scripts/ecommerce-order-flow.jmx`
- 用户 CSV: `scripts/ecommerce-order-users.csv`
- Mock 接口服务: `mock-server.py`

## 业务流程

1. 多用户登录: `POST /api/ecommerce/auth/login`
2. 查询商品: `GET /api/ecommerce/products/search?q={keyword}&page=1&size=5`
3. 选择商品/查商品详情: `GET /api/ecommerce/products/{product_id}`
4. 加入购物车: `POST /api/ecommerce/cart/items`
5. 创建订单: `POST /api/ecommerce/orders`
6. 查看订单详情: `GET /api/ecommerce/orders/{order_id}`
7. 取消订单: `POST /api/ecommerce/orders/{order_id}/cancel`

## CSV 字段

```csv
username,password,email,keyword,quantity
```

`mock-server.py` 启动后会自动预置 `orderuser001` 到 `orderuser100`，密码为 `orderpass001` 到 `orderpass100`，可直接用 `scripts/ecommerce-order-users.csv` 登录。

## 本地运行

启动 Mock 服务:

```bash
MOCK_PORT=9100 python3 mock-server.py
```

运行 JMeter:

```bash
mkdir -p reports/ecommerce-order-flow

apache-jmeter-5.6.3/bin/jmeter \
  -n \
  -t scripts/ecommerce-order-flow.jmx \
  -l reports/ecommerce-order-flow/result.jtl \
  -j reports/ecommerce-order-flow/jmeter.log
```

常用参数覆盖:

```bash
mkdir -p reports/ecommerce-order-flow

apache-jmeter-5.6.3/bin/jmeter \
  -n \
  -t scripts/ecommerce-order-flow.jmx \
  -l reports/ecommerce-order-flow/result.jtl \
  -j reports/ecommerce-order-flow/jmeter.log \
  -Jmock_host=127.0.0.1 \
  -Jmock_port=9100 \
  -Jusers_csv=scripts/ecommerce-order-users.csv \
  -Jthreads=20 \
  -Jramp_time=10 \
  -Jduration=300 \
  -Jloops=-1
```

## 平台中使用

本地 JMeter 可以直接使用上面的两个文件。平台 UI 的脚本和 CSV 下拉列表来自数据库，首次使用时需要上传:

- 脚本: `scripts/ecommerce-order-flow.jmx`
- CSV: `scripts/ecommerce-order-users.csv`

上传后即可在创建任务时直接选择这两个文件。CSV 变量名保持:

```text
username,password,email,keyword,quantity
```

脚本本身也内置了同目录 CSV 引用；如果不通过平台额外注入 CSV，本地运行仍可正常读取 `scripts/ecommerce-order-users.csv`。
