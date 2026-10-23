BEGIN TRANSACTION;
CREATE TABLE logistics (
    order_id     TEXT PRIMARY KEY REFERENCES orders(order_id),
    carrier      TEXT NOT NULL,
    tracking_no  TEXT NOT NULL,
    status       TEXT NOT NULL,
    current_city TEXT,
    updated_at   TEXT NOT NULL,
    trace_json   TEXT NOT NULL DEFAULT '[]'
);
INSERT INTO "logistics" VALUES('O10023','京东物流','SF10023CN','in_transit','广州','2026-10-22','[{"time": "2026-10-20", "text": "商家已发货，等待揽收"}, {"time": "2026-10-21", "text": "已揽收，发往广州分拨中心"}, {"time": "2026-10-22", "text": "到达【广州分拨中心】，正在分拣"}]');
INSERT INTO "logistics" VALUES('O10024','顺丰速运','SF10024CN','delivered',NULL,'2026-10-20','[{"time": "2026-10-19", "text": "商家已发货，等待揽收"}, {"time": "2026-10-19", "text": "已揽收，发往上海分拨中心"}, {"time": "2026-10-19", "text": "到达【上海分拨中心】，正在分拣"}, {"time": "2026-10-19", "text": "已到达【上海营业点】，正在派送"}, {"time": "2026-10-20", "text": "已签收，签收人：本人。感谢使用顺丰速运"}]');
INSERT INTO "logistics" VALUES('O10025','中通快递','SF10025CN','delivered',NULL,'2026-08-25','[{"time": "2026-08-24", "text": "商家已发货，等待揽收"}, {"time": "2026-08-24", "text": "已揽收，发往杭州分拨中心"}, {"time": "2026-08-24", "text": "到达【杭州分拨中心】，正在分拣"}, {"time": "2026-08-24", "text": "已到达【杭州营业点】，正在派送"}, {"time": "2026-08-25", "text": "已签收，签收人：本人。感谢使用中通快递"}]');
INSERT INTO "logistics" VALUES('O10026','圆通速递','SF10026CN','pending_pickup','苏州','2026-10-21','[{"time": "2026-10-21", "text": "订单待发货，尚未揽收"}]');
INSERT INTO "logistics" VALUES('O10027','京东物流','SF10027CN','delivered',NULL,'2026-10-14','[{"time": "2026-10-13", "text": "商家已发货，等待揽收"}, {"time": "2026-10-13", "text": "已揽收，发往南京分拨中心"}, {"time": "2026-10-13", "text": "到达【南京分拨中心】，正在分拣"}, {"time": "2026-10-13", "text": "已到达【南京营业点】，正在派送"}, {"time": "2026-10-14", "text": "已签收，签收人：本人。感谢使用京东物流"}]');
INSERT INTO "logistics" VALUES('O10028','顺丰速运','SF10028CN','in_transit','北京','2026-10-19','[{"time": "2026-10-18", "text": "商家已发货，等待揽收"}, {"time": "2026-10-19", "text": "已揽收，发往北京分拨中心"}]');
INSERT INTO "logistics" VALUES('O10029','中通快递','SF10029CN','delivered',NULL,'2026-10-12','[{"time": "2026-10-11", "text": "商家已发货，等待揽收"}, {"time": "2026-10-11", "text": "已揽收，发往广州分拨中心"}, {"time": "2026-10-11", "text": "到达【广州分拨中心】，正在分拣"}, {"time": "2026-10-11", "text": "已到达【广州营业点】，正在派送"}, {"time": "2026-10-12", "text": "已签收，签收人：本人。感谢使用中通快递"}]');
INSERT INTO "logistics" VALUES('O10030','圆通速递','SF10030CN','in_transit','上海','2026-10-20','[{"time": "2026-10-19", "text": "商家已发货，等待揽收"}, {"time": "2026-10-20", "text": "已揽收，发往上海分拨中心"}]');
INSERT INTO "logistics" VALUES('O10031','京东物流','SF10031CN','delivered',NULL,'2026-09-24','[{"time": "2026-09-23", "text": "商家已发货，等待揽收"}, {"time": "2026-09-23", "text": "已揽收，发往杭州分拨中心"}, {"time": "2026-09-23", "text": "到达【杭州分拨中心】，正在分拣"}, {"time": "2026-09-23", "text": "已到达【杭州营业点】，正在派送"}, {"time": "2026-09-24", "text": "已签收，签收人：本人。感谢使用京东物流"}]');
INSERT INTO "logistics" VALUES('O10033','中通快递','SF10033CN','pending_pickup','南京','2026-10-21','[{"time": "2026-10-21", "text": "订单待发货，尚未揽收"}]');
INSERT INTO "logistics" VALUES('O10034','圆通速递','SF10034CN','delivered',NULL,'2026-07-26','[{"time": "2026-07-25", "text": "商家已发货，等待揽收"}, {"time": "2026-07-25", "text": "已揽收，发往北京分拨中心"}, {"time": "2026-07-25", "text": "到达【北京分拨中心】，正在分拣"}, {"time": "2026-07-25", "text": "已到达【北京营业点】，正在派送"}, {"time": "2026-07-26", "text": "已签收，签收人：本人。感谢使用圆通速递"}]');
INSERT INTO "logistics" VALUES('O10035','京东物流','SF10035CN','delivered',NULL,'2026-09-09','[{"time": "2026-09-08", "text": "商家已发货，等待揽收"}, {"time": "2026-09-08", "text": "已揽收，发往广州分拨中心"}, {"time": "2026-09-08", "text": "到达【广州分拨中心】，正在分拣"}, {"time": "2026-09-08", "text": "已到达【广州营业点】，正在派送"}, {"time": "2026-09-09", "text": "已签收，签收人：本人。感谢使用京东物流"}]');
INSERT INTO "logistics" VALUES('O10036','顺丰速运','SF10036CN','in_transit','上海','2026-10-19','[{"time": "2026-10-17", "text": "商家已发货，等待揽收"}, {"time": "2026-10-18", "text": "已揽收，发往上海分拨中心"}, {"time": "2026-10-19", "text": "到达【上海分拨中心】，正在分拣"}]');
INSERT INTO "logistics" VALUES('O10037','中通快递','SF10037CN','delivered',NULL,'2026-08-15','[{"time": "2026-08-14", "text": "商家已发货，等待揽收"}, {"time": "2026-08-14", "text": "已揽收，发往杭州分拨中心"}, {"time": "2026-08-14", "text": "到达【杭州分拨中心】，正在分拣"}, {"time": "2026-08-14", "text": "已到达【杭州营业点】，正在派送"}, {"time": "2026-08-15", "text": "已签收，签收人：本人。感谢使用中通快递"}]');
INSERT INTO "logistics" VALUES('O10038','圆通速递','SF10038CN','delivered',NULL,'2026-10-02','[{"time": "2026-10-01", "text": "商家已发货，等待揽收"}, {"time": "2026-10-01", "text": "已揽收，发往苏州分拨中心"}, {"time": "2026-10-01", "text": "到达【苏州分拨中心】，正在分拣"}, {"time": "2026-10-01", "text": "已到达【苏州营业点】，正在派送"}, {"time": "2026-10-02", "text": "已签收，签收人：本人。感谢使用圆通速递"}]');
INSERT INTO "logistics" VALUES('O10041','中通快递','SF10041CN','in_transit','广州','2026-10-17','[{"time": "2026-10-16", "text": "商家已发货，等待揽收"}, {"time": "2026-10-17", "text": "已揽收，发往广州分拨中心"}]');
INSERT INTO "logistics" VALUES('O10042','圆通速递','SF10042CN','delivered',NULL,'2026-07-16','[{"time": "2026-07-15", "text": "商家已发货，等待揽收"}, {"time": "2026-07-15", "text": "已揽收，发往上海分拨中心"}, {"time": "2026-07-15", "text": "到达【上海分拨中心】，正在分拣"}, {"time": "2026-07-15", "text": "已到达【上海营业点】，正在派送"}, {"time": "2026-07-16", "text": "已签收，签收人：本人。感谢使用圆通速递"}]');
INSERT INTO "logistics" VALUES('O10043','京东物流','SF10043CN','in_transit','杭州','2026-10-17','[{"time": "2026-10-15", "text": "商家已发货，等待揽收"}, {"time": "2026-10-16", "text": "已揽收，发往杭州分拨中心"}, {"time": "2026-10-17", "text": "到达【杭州分拨中心】，正在分拣"}]');
INSERT INTO "logistics" VALUES('O10044','顺丰速运','SF10044CN','delivered',NULL,'2026-10-19','[{"time": "2026-10-18", "text": "商家已发货，等待揽收"}, {"time": "2026-10-18", "text": "已揽收，发往苏州分拨中心"}, {"time": "2026-10-18", "text": "到达【苏州分拨中心】，正在分拣"}, {"time": "2026-10-18", "text": "已到达【苏州营业点】，正在派送"}, {"time": "2026-10-19", "text": "已签收，签收人：本人。感谢使用顺丰速运"}]');
CREATE TABLE order_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       TEXT NOT NULL REFERENCES orders(order_id),
    product_id     TEXT NOT NULL REFERENCES products(product_id),
    quantity       INTEGER NOT NULL,
    purchase_price REAL NOT NULL
);
INSERT INTO "order_items" VALUES(1,'O10021','P003',1,129.0);
INSERT INTO "order_items" VALUES(2,'O10022','P001',1,499.0);
INSERT INTO "order_items" VALUES(3,'O10023','P002',1,899.0);
INSERT INTO "order_items" VALUES(4,'O10024','P001',1,529.0);
INSERT INTO "order_items" VALUES(5,'O10024','P004',1,199.0);
INSERT INTO "order_items" VALUES(6,'O10025','P002',1,949.0);
INSERT INTO "order_items" VALUES(7,'O10026','P003',1,129.0);
INSERT INTO "order_items" VALUES(8,'O10027','P001',1,499.0);
INSERT INTO "order_items" VALUES(9,'O10028','P004',1,199.0);
INSERT INTO "order_items" VALUES(10,'O10029','P001',1,519.0);
INSERT INTO "order_items" VALUES(11,'O10030','P002',1,899.0);
INSERT INTO "order_items" VALUES(12,'O10031','P003',1,119.0);
INSERT INTO "order_items" VALUES(13,'O10032','P004',1,199.0);
INSERT INTO "order_items" VALUES(14,'O10033','P001',1,499.0);
INSERT INTO "order_items" VALUES(15,'O10034','P002',1,899.0);
INSERT INTO "order_items" VALUES(16,'O10035','P003',2,129.0);
INSERT INTO "order_items" VALUES(17,'O10036','P001',1,459.0);
INSERT INTO "order_items" VALUES(18,'O10037','P004',1,189.0);
INSERT INTO "order_items" VALUES(19,'O10038','P002',1,869.0);
INSERT INTO "order_items" VALUES(20,'O10039','P001',1,499.0);
INSERT INTO "order_items" VALUES(21,'O10040','P003',1,129.0);
INSERT INTO "order_items" VALUES(22,'O10041','P002',1,899.0);
INSERT INTO "order_items" VALUES(23,'O10042','P003',1,129.0);
INSERT INTO "order_items" VALUES(24,'O10043','P004',1,199.0);
INSERT INTO "order_items" VALUES(25,'O10044','P002',1,929.0);
INSERT INTO "order_items" VALUES(26,'O10045','P004',1,199.0);
CREATE TABLE orders (
    order_id     TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    status       TEXT NOT NULL,
    total_amount REAL NOT NULL,
    paid_amount  REAL NOT NULL,
    created_at   TEXT NOT NULL,
    shipped_at   TEXT,
    delivered_at TEXT,
    cancelled_at TEXT
);
INSERT INTO "orders" VALUES('O10021','USER001','delivered',129.0,129.0,'2026-09-12','2026-09-13','2026-09-14',NULL);
INSERT INTO "orders" VALUES('O10022','USER001','cancelled',499.0,0.0,'2026-10-02',NULL,NULL,'2026-10-02');
INSERT INTO "orders" VALUES('O10023','USER001','shipped',899.0,899.0,'2026-10-19','2026-10-20',NULL,NULL);
INSERT INTO "orders" VALUES('O10024','USER001','delivered',728.0,728.0,'2026-10-18','2026-10-19','2026-10-20',NULL);
INSERT INTO "orders" VALUES('O10025','USER001','delivered',949.0,949.0,'2026-08-23','2026-08-24','2026-08-25',NULL);
INSERT INTO "orders" VALUES('O10026','USER001','pending',129.0,129.0,'2026-10-21',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('O10027','USER001','delivered',499.0,499.0,'2026-10-12','2026-10-13','2026-10-14',NULL);
INSERT INTO "orders" VALUES('O10028','USER001','shipped',199.0,199.0,'2026-10-17','2026-10-18',NULL,NULL);
INSERT INTO "orders" VALUES('O10029','USER002','delivered',519.0,519.0,'2026-10-10','2026-10-11','2026-10-12',NULL);
INSERT INTO "orders" VALUES('O10030','USER002','shipped',899.0,899.0,'2026-10-18','2026-10-19',NULL,NULL);
INSERT INTO "orders" VALUES('O10031','USER002','delivered',119.0,119.0,'2026-09-22','2026-09-23','2026-09-24',NULL);
INSERT INTO "orders" VALUES('O10032','USER002','cancelled',199.0,0.0,'2026-09-27',NULL,NULL,'2026-09-27');
INSERT INTO "orders" VALUES('O10033','USER002','pending',499.0,499.0,'2026-10-21',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('O10034','USER002','delivered',899.0,899.0,'2026-07-24','2026-07-25','2026-07-26',NULL);
INSERT INTO "orders" VALUES('O10035','USER003','delivered',258.0,258.0,'2026-09-07','2026-09-08','2026-09-09',NULL);
INSERT INTO "orders" VALUES('O10036','USER003','shipped',459.0,459.0,'2026-10-16','2026-10-17',NULL,NULL);
INSERT INTO "orders" VALUES('O10037','USER003','delivered',189.0,189.0,'2026-08-13','2026-08-14','2026-08-15',NULL);
INSERT INTO "orders" VALUES('O10038','USER003','delivered',869.0,869.0,'2026-09-30','2026-10-01','2026-10-02',NULL);
INSERT INTO "orders" VALUES('O10039','USER003','cancelled',499.0,0.0,'2026-10-04',NULL,NULL,'2026-10-04');
INSERT INTO "orders" VALUES('O10040','USER003','pending',129.0,129.0,'2026-10-20',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('O10041','USER001','shipped',899.0,899.0,'2026-10-15','2026-10-16',NULL,NULL);
INSERT INTO "orders" VALUES('O10042','USER001','delivered',129.0,129.0,'2026-07-14','2026-07-15','2026-07-16',NULL);
INSERT INTO "orders" VALUES('O10043','USER002','shipped',199.0,199.0,'2026-10-14','2026-10-15',NULL,NULL);
INSERT INTO "orders" VALUES('O10044','USER002','delivered',929.0,929.0,'2026-10-17','2026-10-18','2026-10-19',NULL);
INSERT INTO "orders" VALUES('O10045','USER003','pending',199.0,199.0,'2026-10-21',NULL,NULL,NULL);
CREATE TABLE price_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    price      REAL NOT NULL,
    changed_at TEXT NOT NULL
);
INSERT INTO "price_history" VALUES(1,'P002',949.0,'2026-05-25');
INSERT INTO "price_history" VALUES(2,'P003',119.0,'2026-06-04');
INSERT INTO "price_history" VALUES(3,'P001',499.0,'2026-06-24');
INSERT INTO "price_history" VALUES(4,'P004',189.0,'2026-07-04');
INSERT INTO "price_history" VALUES(5,'P003',129.0,'2026-07-14');
INSERT INTO "price_history" VALUES(6,'P002',899.0,'2026-07-24');
INSERT INTO "price_history" VALUES(7,'P004',189.0,'2026-08-13');
INSERT INTO "price_history" VALUES(8,'P002',949.0,'2026-08-23');
INSERT INTO "price_history" VALUES(9,'P003',129.0,'2026-09-07');
INSERT INTO "price_history" VALUES(10,'P003',129.0,'2026-09-12');
INSERT INTO "price_history" VALUES(11,'P003',119.0,'2026-09-22');
INSERT INTO "price_history" VALUES(12,'P001',529.0,'2026-09-27');
INSERT INTO "price_history" VALUES(13,'P004',199.0,'2026-09-27');
INSERT INTO "price_history" VALUES(14,'P002',869.0,'2026-09-30');
INSERT INTO "price_history" VALUES(15,'P002',899.0,'2026-10-02');
INSERT INTO "price_history" VALUES(16,'P001',499.0,'2026-10-02');
INSERT INTO "price_history" VALUES(17,'P001',499.0,'2026-10-04');
INSERT INTO "price_history" VALUES(18,'P004',199.0,'2026-10-07');
INSERT INTO "price_history" VALUES(19,'P001',519.0,'2026-10-10');
INSERT INTO "price_history" VALUES(20,'P001',499.0,'2026-10-12');
INSERT INTO "price_history" VALUES(21,'P004',199.0,'2026-10-14');
INSERT INTO "price_history" VALUES(22,'P002',899.0,'2026-10-15');
INSERT INTO "price_history" VALUES(23,'P001',459.0,'2026-10-16');
INSERT INTO "price_history" VALUES(24,'P004',199.0,'2026-10-17');
INSERT INTO "price_history" VALUES(25,'P002',929.0,'2026-10-17');
INSERT INTO "price_history" VALUES(26,'P001',529.0,'2026-10-18');
INSERT INTO "price_history" VALUES(27,'P004',199.0,'2026-10-18');
INSERT INTO "price_history" VALUES(28,'P002',899.0,'2026-10-18');
INSERT INTO "price_history" VALUES(29,'P002',899.0,'2026-10-19');
INSERT INTO "price_history" VALUES(30,'P003',129.0,'2026-10-20');
INSERT INTO "price_history" VALUES(31,'P003',129.0,'2026-10-21');
INSERT INTO "price_history" VALUES(32,'P001',499.0,'2026-10-21');
INSERT INTO "price_history" VALUES(33,'P004',199.0,'2026-10-21');
INSERT INTO "price_history" VALUES(34,'P001',469.0,'2026-10-22');
INSERT INTO "price_history" VALUES(35,'P002',879.0,'2026-10-22');
INSERT INTO "price_history" VALUES(36,'P003',129.0,'2026-10-22');
INSERT INTO "price_history" VALUES(37,'P004',179.0,'2026-10-22');
CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    brand      TEXT NOT NULL,
    price      REAL NOT NULL,
    currency   TEXT NOT NULL DEFAULT 'CNY',
    attributes TEXT NOT NULL DEFAULT ''
);
INSERT INTO "products" VALUES('P001','SoundCore 声阔 Life P3 无线降噪耳机','无线耳机','SoundCore',499.0,'CNY','蓝牙5.2；主动降噪；单次续航10小时；总续航50小时；入耳式；IPX5防水；USB-C充电');
INSERT INTO "products" VALUES('P002','SoundCore 声阔 Space Q45 头戴式耳机','无线耳机','SoundCore',899.0,'CNY','蓝牙5.3；自适应降噪；单次续航50小时；头戴式；支持LDAC；可折叠；3.5mm有线模式');
INSERT INTO "products" VALUES('P003','Anker 安克 65W GaN 氮化镓充电器','充电配件','Anker',129.0,'CNY','65W；2C1A三口；GaN氮化镓；支持PD3.0/QC4+；折叠插脚；体积比传统充电器小40%');
INSERT INTO "products" VALUES('P004','Anker 安克 10000mAh 磁吸无线充电宝','充电配件','Anker',199.0,'CNY','10000mAh；磁吸无线充电；15W无线+20W有线；支持边充边放；兼容MagSafe');
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    phone   TEXT NOT NULL
);
INSERT INTO "users" VALUES('USER001','张伟','13800138001');
INSERT INTO "users" VALUES('USER002','李娜','13900139002');
INSERT INTO "users" VALUES('USER003','王芳','13700137003');
CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_price_product ON price_history(product_id, changed_at);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('order_items',26);
INSERT INTO "sqlite_sequence" VALUES('price_history',37);
COMMIT;
