BEGIN TRANSACTION;
CREATE TABLE acs_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
INSERT INTO "acs_migrations" VALUES(1,'2026-10-23T01:59:15.525535+00:00');
INSERT INTO "acs_migrations" VALUES(2,'2026-10-23T01:59:15.525624+00:00');
INSERT INTO "acs_migrations" VALUES(3,'2026-10-23T01:59:15.526149+00:00');
INSERT INTO "acs_migrations" VALUES(4,'2026-10-23T01:59:15.526313+00:00');
INSERT INTO "acs_migrations" VALUES(5,'2026-10-23T01:59:15.526441+00:00');
INSERT INTO "acs_migrations" VALUES(6,'2026-10-23T01:59:15.526526+00:00');
CREATE TABLE conversation_feedback (feedback_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversations(session_id), turn_id TEXT NOT NULL REFERENCES conversation_turns(turn_id), user_id TEXT NOT NULL REFERENCES users(user_id), value TEXT NOT NULL CHECK(value IN ('helpful','unresolved')), created_at TEXT NOT NULL);
CREATE TABLE conversation_turns (turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversations(session_id), sequence INTEGER NOT NULL,user_text TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',status TEXT NOT NULL CHECK(status IN ('running','completed','interrupted')),messages_json TEXT NOT NULL DEFAULT '[]', result_json TEXT,created_at TEXT NOT NULL, finished_at TEXT,UNIQUE(session_id, sequence));
INSERT INTO "conversation_turns" VALUES('ea9f680bf0884e3494033f8eacd89898','0a9367a9a3884116ba7a8a9b49771922',1,'[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，剩余数量需核查。','合成场景预置：已创建1件商品的待审核申请，尚未退款；不是本轮模型生成。','completed','[{"role": "user", "content": "[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，剩余数量需核查。"}, {"role": "assistant", "content": "合成场景预置：已创建1件商品的待审核申请，尚未退款；不是本轮模型生成。"}]','{"answer": "合成场景预置：已创建1件商品的待审核申请，尚未退款；不是本轮模型生成。", "stop_reason": "answered", "trace": [{"type": "ui_action", "label": "synthetic_seed", "detail": {"order_id": "S20001"}}]}','2026-10-23T01:59:16.833483+00:00','2026-10-23T01:59:18.374454+00:00');
CREATE TABLE conversations (session_id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(user_id),title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "conversations" VALUES('0a9367a9a3884116ba7a8a9b49771922','USER001','[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，','2026-10-23T01:59:16.484107+00:00','2026-10-23T01:59:18.374652+00:00');
CREATE TABLE logistics (
    order_id     TEXT PRIMARY KEY REFERENCES orders(order_id),
    carrier      TEXT NOT NULL,
    tracking_no  TEXT NOT NULL,
    status       TEXT NOT NULL,
    current_city TEXT,
    updated_at   TEXT NOT NULL,
    trace_json   TEXT NOT NULL DEFAULT '[]'
);
INSERT INTO "logistics" VALUES('S20001','演示物流','SYNS20001','delivered',NULL,'2026-10-15','[{"time": "2026-10-14", "text": "演示包裹已揽收"}, {"time": "2026-10-15", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20002','演示物流','SYNS20002','delivered',NULL,'2026-10-15','[{"time": "2026-10-14", "text": "演示包裹已揽收"}, {"time": "2026-10-15", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20003','演示物流','SYNS20003','shipped','演示中转站','2026-10-13','[{"time": "2026-10-13", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20006','演示物流','SYNS20006','delivered',NULL,'2026-10-14','[{"time": "2026-10-13", "text": "演示包裹已揽收"}, {"time": "2026-10-14", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20007','演示物流','SYNS20007','delivered',NULL,'2026-10-14','[{"time": "2026-10-13", "text": "演示包裹已揽收"}, {"time": "2026-10-14", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20008','演示物流','SYNS20008','shipped','演示中转站','2026-10-14','[{"time": "2026-10-14", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20011','演示物流','SYNS20011','delivered',NULL,'2026-10-09','[{"time": "2026-10-08", "text": "演示包裹已揽收"}, {"time": "2026-10-09", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20012','演示物流','SYNS20012','delivered',NULL,'2026-10-09','[{"time": "2026-10-08", "text": "演示包裹已揽收"}, {"time": "2026-10-09", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20013','演示物流','SYNS20013','shipped','演示中转站','2026-10-15','[{"time": "2026-10-15", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20016','演示物流','SYNS20016','delivered',NULL,'2026-10-08','[{"time": "2026-10-07", "text": "演示包裹已揽收"}, {"time": "2026-10-08", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20017','演示物流','SYNS20017','delivered',NULL,'2026-10-08','[{"time": "2026-10-07", "text": "演示包裹已揽收"}, {"time": "2026-10-08", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20018','演示物流','SYNS20018','shipped','演示中转站','2026-10-13','[{"time": "2026-10-13", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20021','演示物流','SYNS20021','delivered',NULL,'2026-10-07','[{"time": "2026-10-06", "text": "演示包裹已揽收"}, {"time": "2026-10-07", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20022','演示物流','SYNS20022','delivered',NULL,'2026-10-07','[{"time": "2026-10-06", "text": "演示包裹已揽收"}, {"time": "2026-10-07", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20023','演示物流','SYNS20023','shipped','演示中转站','2026-10-14','[{"time": "2026-10-14", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20026','演示物流','SYNS20026','delivered',NULL,'2026-10-01','[{"time": "2026-09-30", "text": "演示包裹已揽收"}, {"time": "2026-10-01", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20027','演示物流','SYNS20027','delivered',NULL,'2026-10-01','[{"time": "2026-09-30", "text": "演示包裹已揽收"}, {"time": "2026-10-01", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20028','演示物流','SYNS20028','shipped','演示中转站','2026-10-15','[{"time": "2026-10-15", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20031','演示物流','SYNS20031','delivered',NULL,'2026-09-30','[{"time": "2026-09-29", "text": "演示包裹已揽收"}, {"time": "2026-09-30", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20032','演示物流','SYNS20032','delivered',NULL,'2026-09-30','[{"time": "2026-09-29", "text": "演示包裹已揽收"}, {"time": "2026-09-30", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20033','演示物流','SYNS20033','shipped','演示中转站','2026-10-13','[{"time": "2026-10-13", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20036','演示物流','SYNS20036','delivered',NULL,'2026-09-29','[{"time": "2026-09-28", "text": "演示包裹已揽收"}, {"time": "2026-09-29", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20037','演示物流','SYNS20037','delivered',NULL,'2026-09-29','[{"time": "2026-09-28", "text": "演示包裹已揽收"}, {"time": "2026-09-29", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20038','演示物流','SYNS20038','shipped','演示中转站','2026-10-14','[{"time": "2026-10-14", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20041','演示物流','SYNS20041','delivered',NULL,'2026-09-15','[{"time": "2026-09-14", "text": "演示包裹已揽收"}, {"time": "2026-09-15", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20042','演示物流','SYNS20042','delivered',NULL,'2026-09-15','[{"time": "2026-09-14", "text": "演示包裹已揽收"}, {"time": "2026-09-15", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20043','演示物流','SYNS20043','shipped','演示中转站','2026-10-15','[{"time": "2026-10-15", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20046','演示物流','SYNS20046','delivered',NULL,'2026-10-15','[{"time": "2026-10-14", "text": "演示包裹已揽收"}, {"time": "2026-10-15", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20047','演示物流','SYNS20047','delivered',NULL,'2026-10-15','[{"time": "2026-10-14", "text": "演示包裹已揽收"}, {"time": "2026-10-15", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20048','演示物流','SYNS20048','shipped','演示中转站','2026-10-13','[{"time": "2026-10-13", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20051','演示物流','SYNS20051','delivered',NULL,'2026-10-14','[{"time": "2026-10-13", "text": "演示包裹已揽收"}, {"time": "2026-10-14", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20052','演示物流','SYNS20052','delivered',NULL,'2026-10-14','[{"time": "2026-10-13", "text": "演示包裹已揽收"}, {"time": "2026-10-14", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20053','演示物流','SYNS20053','shipped','演示中转站','2026-10-14','[{"time": "2026-10-14", "text": "演示包裹已揽收"}]');
INSERT INTO "logistics" VALUES('S20056','演示物流','SYNS20056','delivered',NULL,'2026-10-09','[{"time": "2026-10-08", "text": "演示包裹已揽收"}, {"time": "2026-10-09", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20057','演示物流','SYNS20057','delivered',NULL,'2026-10-09','[{"time": "2026-10-08", "text": "演示包裹已揽收"}, {"time": "2026-10-09", "text": "演示包裹已签收"}]');
INSERT INTO "logistics" VALUES('S20058','演示物流','SYNS20058','shipped','演示中转站','2026-10-15','[{"time": "2026-10-15", "text": "演示包裹已揽收"}]');
CREATE TABLE order_item_skus (item_id INTEGER PRIMARY KEY REFERENCES order_items(id),sku_id TEXT NOT NULL REFERENCES product_skus(sku_id));
INSERT INTO "order_item_skus" VALUES(1,'V204-2');
INSERT INTO "order_item_skus" VALUES(2,'V212-2');
INSERT INTO "order_item_skus" VALUES(3,'V201-1');
INSERT INTO "order_item_skus" VALUES(4,'V201-1');
INSERT INTO "order_item_skus" VALUES(5,'V209-2');
INSERT INTO "order_item_skus" VALUES(6,'V207-2');
INSERT INTO "order_item_skus" VALUES(7,'V210-1');
INSERT INTO "order_item_skus" VALUES(8,'V208-1');
INSERT INTO "order_item_skus" VALUES(9,'V204-1');
INSERT INTO "order_item_skus" VALUES(10,'V201-2');
INSERT INTO "order_item_skus" VALUES(11,'V202-1');
INSERT INTO "order_item_skus" VALUES(12,'V206-2');
INSERT INTO "order_item_skus" VALUES(13,'V210-2');
INSERT INTO "order_item_skus" VALUES(14,'V203-1');
INSERT INTO "order_item_skus" VALUES(15,'V201-1');
INSERT INTO "order_item_skus" VALUES(16,'V209-2');
INSERT INTO "order_item_skus" VALUES(17,'V208-1');
INSERT INTO "order_item_skus" VALUES(18,'V201-2');
INSERT INTO "order_item_skus" VALUES(19,'V208-1');
INSERT INTO "order_item_skus" VALUES(20,'V202-1');
INSERT INTO "order_item_skus" VALUES(21,'V204-1');
INSERT INTO "order_item_skus" VALUES(22,'V209-2');
INSERT INTO "order_item_skus" VALUES(23,'V209-1');
INSERT INTO "order_item_skus" VALUES(24,'V208-1');
INSERT INTO "order_item_skus" VALUES(25,'V208-1');
INSERT INTO "order_item_skus" VALUES(26,'V210-1');
INSERT INTO "order_item_skus" VALUES(27,'V205-2');
INSERT INTO "order_item_skus" VALUES(28,'V203-1');
INSERT INTO "order_item_skus" VALUES(29,'V202-2');
INSERT INTO "order_item_skus" VALUES(30,'V201-1');
INSERT INTO "order_item_skus" VALUES(31,'V207-2');
INSERT INTO "order_item_skus" VALUES(32,'V209-1');
INSERT INTO "order_item_skus" VALUES(33,'V207-2');
INSERT INTO "order_item_skus" VALUES(34,'V208-1');
INSERT INTO "order_item_skus" VALUES(35,'V205-1');
INSERT INTO "order_item_skus" VALUES(36,'V201-2');
INSERT INTO "order_item_skus" VALUES(37,'V210-1');
INSERT INTO "order_item_skus" VALUES(38,'V204-2');
INSERT INTO "order_item_skus" VALUES(39,'V211-2');
INSERT INTO "order_item_skus" VALUES(40,'V209-2');
INSERT INTO "order_item_skus" VALUES(41,'V205-1');
INSERT INTO "order_item_skus" VALUES(42,'V204-2');
INSERT INTO "order_item_skus" VALUES(43,'V211-1');
INSERT INTO "order_item_skus" VALUES(44,'V212-2');
INSERT INTO "order_item_skus" VALUES(45,'V210-1');
INSERT INTO "order_item_skus" VALUES(46,'V207-1');
INSERT INTO "order_item_skus" VALUES(47,'V208-1');
INSERT INTO "order_item_skus" VALUES(48,'V202-2');
INSERT INTO "order_item_skus" VALUES(49,'V206-2');
INSERT INTO "order_item_skus" VALUES(50,'V211-1');
INSERT INTO "order_item_skus" VALUES(51,'V201-1');
INSERT INTO "order_item_skus" VALUES(52,'V202-1');
INSERT INTO "order_item_skus" VALUES(53,'V203-1');
INSERT INTO "order_item_skus" VALUES(54,'V205-2');
INSERT INTO "order_item_skus" VALUES(55,'V206-1');
INSERT INTO "order_item_skus" VALUES(56,'V202-2');
INSERT INTO "order_item_skus" VALUES(57,'V203-1');
INSERT INTO "order_item_skus" VALUES(58,'V210-2');
INSERT INTO "order_item_skus" VALUES(59,'V212-2');
INSERT INTO "order_item_skus" VALUES(60,'V207-2');
INSERT INTO "order_item_skus" VALUES(61,'V210-1');
INSERT INTO "order_item_skus" VALUES(62,'V208-1');
INSERT INTO "order_item_skus" VALUES(63,'V211-2');
INSERT INTO "order_item_skus" VALUES(64,'V206-2');
INSERT INTO "order_item_skus" VALUES(65,'V204-1');
INSERT INTO "order_item_skus" VALUES(66,'V203-1');
INSERT INTO "order_item_skus" VALUES(67,'V212-2');
INSERT INTO "order_item_skus" VALUES(68,'V201-1');
INSERT INTO "order_item_skus" VALUES(69,'V203-1');
INSERT INTO "order_item_skus" VALUES(70,'V203-2');
INSERT INTO "order_item_skus" VALUES(71,'V212-2');
INSERT INTO "order_item_skus" VALUES(72,'V205-1');
INSERT INTO "order_item_skus" VALUES(73,'V201-1');
INSERT INTO "order_item_skus" VALUES(74,'V203-2');
INSERT INTO "order_item_skus" VALUES(75,'V206-1');
CREATE TABLE order_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       TEXT NOT NULL REFERENCES orders(order_id),
    product_id     TEXT NOT NULL REFERENCES products(product_id),
    quantity       INTEGER NOT NULL,
    purchase_price REAL NOT NULL
);
INSERT INTO "order_items" VALUES(1,'S20001','V204',2,499.0);
INSERT INTO "order_items" VALUES(2,'S20001','V212',1,219.0);
INSERT INTO "order_items" VALUES(3,'S20002','V201',1,179.0);
INSERT INTO "order_items" VALUES(4,'S20003','V201',1,179.0);
INSERT INTO "order_items" VALUES(5,'S20004','V209',1,119.0);
INSERT INTO "order_items" VALUES(6,'S20005','V207',1,379.0);
INSERT INTO "order_items" VALUES(7,'S20005','V210',1,79.0);
INSERT INTO "order_items" VALUES(8,'S20006','V208',2,499.0);
INSERT INTO "order_items" VALUES(9,'S20007','V204',1,549.0);
INSERT INTO "order_items" VALUES(10,'S20008','V201',1,179.0);
INSERT INTO "order_items" VALUES(11,'S20009','V202',1,369.0);
INSERT INTO "order_items" VALUES(12,'S20009','V206',1,279.0);
INSERT INTO "order_items" VALUES(13,'S20010','V210',1,79.0);
INSERT INTO "order_items" VALUES(14,'S20011','V203',2,149.0);
INSERT INTO "order_items" VALUES(15,'S20012','V201',1,199.0);
INSERT INTO "order_items" VALUES(16,'S20013','V209',1,119.0);
INSERT INTO "order_items" VALUES(17,'S20013','V208',1,469.0);
INSERT INTO "order_items" VALUES(18,'S20014','V201',1,179.0);
INSERT INTO "order_items" VALUES(19,'S20015','V208',1,469.0);
INSERT INTO "order_items" VALUES(20,'S20016','V202',2,399.0);
INSERT INTO "order_items" VALUES(21,'S20017','V204',1,549.0);
INSERT INTO "order_items" VALUES(22,'S20017','V209',1,129.0);
INSERT INTO "order_items" VALUES(23,'S20018','V209',1,119.0);
INSERT INTO "order_items" VALUES(24,'S20019','V208',1,469.0);
INSERT INTO "order_items" VALUES(25,'S20020','V208',1,469.0);
INSERT INTO "order_items" VALUES(26,'S20021','V210',2,79.0);
INSERT INTO "order_items" VALUES(27,'S20021','V205',1,259.0);
INSERT INTO "order_items" VALUES(28,'S20022','V203',1,149.0);
INSERT INTO "order_items" VALUES(29,'S20023','V202',1,369.0);
INSERT INTO "order_items" VALUES(30,'S20024','V201',1,179.0);
INSERT INTO "order_items" VALUES(31,'S20025','V207',1,379.0);
INSERT INTO "order_items" VALUES(32,'S20025','V209',1,119.0);
INSERT INTO "order_items" VALUES(33,'S20026','V207',2,399.0);
INSERT INTO "order_items" VALUES(34,'S20027','V208',1,499.0);
INSERT INTO "order_items" VALUES(35,'S20028','V205',1,239.0);
INSERT INTO "order_items" VALUES(36,'S20029','V201',1,179.0);
INSERT INTO "order_items" VALUES(37,'S20029','V210',1,79.0);
INSERT INTO "order_items" VALUES(38,'S20030','V204',1,499.0);
INSERT INTO "order_items" VALUES(39,'S20031','V211',2,159.0);
INSERT INTO "order_items" VALUES(40,'S20032','V209',1,129.0);
INSERT INTO "order_items" VALUES(41,'S20033','V205',1,239.0);
INSERT INTO "order_items" VALUES(42,'S20033','V204',1,499.0);
INSERT INTO "order_items" VALUES(43,'S20034','V211',1,149.0);
INSERT INTO "order_items" VALUES(44,'S20035','V212',1,219.0);
INSERT INTO "order_items" VALUES(45,'S20036','V210',2,79.0);
INSERT INTO "order_items" VALUES(46,'S20037','V207',1,399.0);
INSERT INTO "order_items" VALUES(47,'S20037','V208',1,499.0);
INSERT INTO "order_items" VALUES(48,'S20038','V202',1,369.0);
INSERT INTO "order_items" VALUES(49,'S20039','V206',1,279.0);
INSERT INTO "order_items" VALUES(50,'S20040','V211',1,149.0);
INSERT INTO "order_items" VALUES(51,'S20041','V201',2,199.0);
INSERT INTO "order_items" VALUES(52,'S20041','V202',1,399.0);
INSERT INTO "order_items" VALUES(53,'S20042','V203',1,149.0);
INSERT INTO "order_items" VALUES(54,'S20043','V205',1,239.0);
INSERT INTO "order_items" VALUES(55,'S20044','V206',1,279.0);
INSERT INTO "order_items" VALUES(56,'S20045','V202',1,369.0);
INSERT INTO "order_items" VALUES(57,'S20045','V203',1,149.0);
INSERT INTO "order_items" VALUES(58,'S20046','V210',2,79.0);
INSERT INTO "order_items" VALUES(59,'S20047','V212',1,219.0);
INSERT INTO "order_items" VALUES(60,'S20048','V207',1,379.0);
INSERT INTO "order_items" VALUES(61,'S20049','V210',1,79.0);
INSERT INTO "order_items" VALUES(62,'S20049','V208',1,469.0);
INSERT INTO "order_items" VALUES(63,'S20050','V211',1,149.0);
INSERT INTO "order_items" VALUES(64,'S20051','V206',2,299.0);
INSERT INTO "order_items" VALUES(65,'S20052','V204',1,549.0);
INSERT INTO "order_items" VALUES(66,'S20053','V203',1,149.0);
INSERT INTO "order_items" VALUES(67,'S20053','V212',1,219.0);
INSERT INTO "order_items" VALUES(68,'S20054','V201',1,179.0);
INSERT INTO "order_items" VALUES(69,'S20055','V203',1,149.0);
INSERT INTO "order_items" VALUES(70,'S20056','V203',2,149.0);
INSERT INTO "order_items" VALUES(71,'S20057','V212',1,239.0);
INSERT INTO "order_items" VALUES(72,'S20057','V205',1,259.0);
INSERT INTO "order_items" VALUES(73,'S20058','V201',1,179.0);
INSERT INTO "order_items" VALUES(74,'S20059','V203',1,149.0);
INSERT INTO "order_items" VALUES(75,'S20060','V206',1,279.0);
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
INSERT INTO "orders" VALUES('S20001','USER001','delivered',1217.0,1217.0,'2026-10-12','2026-10-14','2026-10-15',NULL);
INSERT INTO "orders" VALUES('S20002','USER001','delivered',179.0,179.0,'2026-10-12','2026-10-14','2026-10-15',NULL);
INSERT INTO "orders" VALUES('S20003','USER001','shipped',179.0,179.0,'2026-10-12','2026-10-13',NULL,NULL);
INSERT INTO "orders" VALUES('S20004','USER001','pending',119.0,119.0,'2026-10-14',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20005','USER001','cancelled',458.0,0.0,'2026-10-13',NULL,NULL,'2026-10-13');
INSERT INTO "orders" VALUES('S20006','USER001','delivered',998.0,998.0,'2026-10-11','2026-10-13','2026-10-14',NULL);
INSERT INTO "orders" VALUES('S20007','USER001','delivered',549.0,549.0,'2026-10-11','2026-10-13','2026-10-14',NULL);
INSERT INTO "orders" VALUES('S20008','USER001','shipped',179.0,179.0,'2026-10-13','2026-10-14',NULL,NULL);
INSERT INTO "orders" VALUES('S20009','USER001','pending',648.0,648.0,'2026-10-12',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20010','USER001','cancelled',79.0,0.0,'2026-10-14',NULL,NULL,'2026-10-14');
INSERT INTO "orders" VALUES('S20011','USER001','delivered',298.0,298.0,'2026-10-06','2026-10-08','2026-10-09',NULL);
INSERT INTO "orders" VALUES('S20012','USER001','delivered',199.0,199.0,'2026-10-06','2026-10-08','2026-10-09',NULL);
INSERT INTO "orders" VALUES('S20013','USER001','shipped',588.0,588.0,'2026-10-14','2026-10-15',NULL,NULL);
INSERT INTO "orders" VALUES('S20014','USER001','pending',179.0,179.0,'2026-10-13',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20015','USER001','cancelled',469.0,0.0,'2026-10-12',NULL,NULL,'2026-10-12');
INSERT INTO "orders" VALUES('S20016','USER001','delivered',798.0,798.0,'2026-10-05','2026-10-07','2026-10-08',NULL);
INSERT INTO "orders" VALUES('S20017','USER001','delivered',678.0,678.0,'2026-10-05','2026-10-07','2026-10-08',NULL);
INSERT INTO "orders" VALUES('S20018','USER001','shipped',119.0,119.0,'2026-10-12','2026-10-13',NULL,NULL);
INSERT INTO "orders" VALUES('S20019','USER001','pending',469.0,469.0,'2026-10-14',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20020','USER001','cancelled',469.0,0.0,'2026-10-13',NULL,NULL,'2026-10-13');
INSERT INTO "orders" VALUES('S20021','USER002','delivered',417.0,417.0,'2026-10-04','2026-10-06','2026-10-07',NULL);
INSERT INTO "orders" VALUES('S20022','USER002','delivered',149.0,149.0,'2026-10-04','2026-10-06','2026-10-07',NULL);
INSERT INTO "orders" VALUES('S20023','USER002','shipped',369.0,369.0,'2026-10-13','2026-10-14',NULL,NULL);
INSERT INTO "orders" VALUES('S20024','USER002','pending',179.0,179.0,'2026-10-12',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20025','USER002','cancelled',498.0,0.0,'2026-10-14',NULL,NULL,'2026-10-14');
INSERT INTO "orders" VALUES('S20026','USER002','delivered',798.0,798.0,'2026-09-28','2026-09-30','2026-10-01',NULL);
INSERT INTO "orders" VALUES('S20027','USER002','delivered',499.0,499.0,'2026-09-28','2026-09-30','2026-10-01',NULL);
INSERT INTO "orders" VALUES('S20028','USER002','shipped',239.0,239.0,'2026-10-14','2026-10-15',NULL,NULL);
INSERT INTO "orders" VALUES('S20029','USER002','pending',258.0,258.0,'2026-10-13',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20030','USER002','cancelled',499.0,0.0,'2026-10-12',NULL,NULL,'2026-10-12');
INSERT INTO "orders" VALUES('S20031','USER002','delivered',318.0,318.0,'2026-09-27','2026-09-29','2026-09-30',NULL);
INSERT INTO "orders" VALUES('S20032','USER002','delivered',129.0,129.0,'2026-09-27','2026-09-29','2026-09-30',NULL);
INSERT INTO "orders" VALUES('S20033','USER002','shipped',738.0,738.0,'2026-10-12','2026-10-13',NULL,NULL);
INSERT INTO "orders" VALUES('S20034','USER002','pending',149.0,149.0,'2026-10-14',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20035','USER002','cancelled',219.0,0.0,'2026-10-13',NULL,NULL,'2026-10-13');
INSERT INTO "orders" VALUES('S20036','USER002','delivered',158.0,158.0,'2026-09-26','2026-09-28','2026-09-29',NULL);
INSERT INTO "orders" VALUES('S20037','USER002','delivered',898.0,898.0,'2026-09-26','2026-09-28','2026-09-29',NULL);
INSERT INTO "orders" VALUES('S20038','USER002','shipped',369.0,369.0,'2026-10-13','2026-10-14',NULL,NULL);
INSERT INTO "orders" VALUES('S20039','USER002','pending',279.0,279.0,'2026-10-12',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20040','USER002','cancelled',149.0,0.0,'2026-10-14',NULL,NULL,'2026-10-14');
INSERT INTO "orders" VALUES('S20041','USER003','delivered',797.0,797.0,'2026-09-12','2026-09-14','2026-09-15',NULL);
INSERT INTO "orders" VALUES('S20042','USER003','delivered',149.0,149.0,'2026-09-12','2026-09-14','2026-09-15',NULL);
INSERT INTO "orders" VALUES('S20043','USER003','shipped',239.0,239.0,'2026-10-14','2026-10-15',NULL,NULL);
INSERT INTO "orders" VALUES('S20044','USER003','pending',279.0,279.0,'2026-10-13',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20045','USER003','cancelled',518.0,0.0,'2026-10-12',NULL,NULL,'2026-10-12');
INSERT INTO "orders" VALUES('S20046','USER003','delivered',158.0,158.0,'2026-10-12','2026-10-14','2026-10-15',NULL);
INSERT INTO "orders" VALUES('S20047','USER003','delivered',219.0,219.0,'2026-10-12','2026-10-14','2026-10-15',NULL);
INSERT INTO "orders" VALUES('S20048','USER003','shipped',379.0,379.0,'2026-10-12','2026-10-13',NULL,NULL);
INSERT INTO "orders" VALUES('S20049','USER003','pending',548.0,548.0,'2026-10-14',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20050','USER003','cancelled',149.0,0.0,'2026-10-13',NULL,NULL,'2026-10-13');
INSERT INTO "orders" VALUES('S20051','USER003','delivered',598.0,598.0,'2026-10-11','2026-10-13','2026-10-14',NULL);
INSERT INTO "orders" VALUES('S20052','USER003','delivered',549.0,549.0,'2026-10-11','2026-10-13','2026-10-14',NULL);
INSERT INTO "orders" VALUES('S20053','USER003','shipped',368.0,368.0,'2026-10-13','2026-10-14',NULL,NULL);
INSERT INTO "orders" VALUES('S20054','USER003','pending',179.0,179.0,'2026-10-12',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20055','USER003','cancelled',149.0,0.0,'2026-10-14',NULL,NULL,'2026-10-14');
INSERT INTO "orders" VALUES('S20056','USER003','delivered',298.0,298.0,'2026-10-06','2026-10-08','2026-10-09',NULL);
INSERT INTO "orders" VALUES('S20057','USER003','delivered',498.0,498.0,'2026-10-06','2026-10-08','2026-10-09',NULL);
INSERT INTO "orders" VALUES('S20058','USER003','shipped',179.0,179.0,'2026-10-14','2026-10-15',NULL,NULL);
INSERT INTO "orders" VALUES('S20059','USER003','pending',149.0,149.0,'2026-10-13',NULL,NULL,NULL);
INSERT INTO "orders" VALUES('S20060','USER003','cancelled',279.0,0.0,'2026-10-12',NULL,NULL,'2026-10-12');
CREATE TABLE price_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    price      REAL NOT NULL,
    changed_at TEXT NOT NULL
);
INSERT INTO "price_history" VALUES(1,'V201',199.0,'2026-07-17');
INSERT INTO "price_history" VALUES(2,'V201',179.0,'2026-10-12');
INSERT INTO "price_history" VALUES(3,'V202',399.0,'2026-07-17');
INSERT INTO "price_history" VALUES(4,'V202',369.0,'2026-10-12');
INSERT INTO "price_history" VALUES(5,'V203',149.0,'2026-07-17');
INSERT INTO "price_history" VALUES(6,'V203',149.0,'2026-10-12');
INSERT INTO "price_history" VALUES(7,'V204',549.0,'2026-07-17');
INSERT INTO "price_history" VALUES(8,'V204',499.0,'2026-10-12');
INSERT INTO "price_history" VALUES(9,'V205',259.0,'2026-07-17');
INSERT INTO "price_history" VALUES(10,'V205',239.0,'2026-10-12');
INSERT INTO "price_history" VALUES(11,'V206',299.0,'2026-07-17');
INSERT INTO "price_history" VALUES(12,'V206',279.0,'2026-10-12');
INSERT INTO "price_history" VALUES(13,'V207',399.0,'2026-07-17');
INSERT INTO "price_history" VALUES(14,'V207',379.0,'2026-10-12');
INSERT INTO "price_history" VALUES(15,'V208',499.0,'2026-07-17');
INSERT INTO "price_history" VALUES(16,'V208',469.0,'2026-10-12');
INSERT INTO "price_history" VALUES(17,'V209',129.0,'2026-07-17');
INSERT INTO "price_history" VALUES(18,'V209',119.0,'2026-10-12');
INSERT INTO "price_history" VALUES(19,'V210',79.0,'2026-07-17');
INSERT INTO "price_history" VALUES(20,'V210',79.0,'2026-10-12');
INSERT INTO "price_history" VALUES(21,'V211',159.0,'2026-07-17');
INSERT INTO "price_history" VALUES(22,'V211',149.0,'2026-10-12');
INSERT INTO "price_history" VALUES(23,'V212',239.0,'2026-07-17');
INSERT INTO "price_history" VALUES(24,'V212',219.0,'2026-10-12');
CREATE TABLE product_skus (sku_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(product_id), wearing_type TEXT, anc INTEGER CHECK(anc IN (0,1)), color TEXT, availability TEXT NOT NULL CHECK(availability IN ('available','unavailable','unknown')));
INSERT INTO "product_skus" VALUES('V201-1','V201','入耳式',1,'黑色','available');
INSERT INTO "product_skus" VALUES('V201-2','V201','入耳式',1,'白色','available');
INSERT INTO "product_skus" VALUES('V202-1','V202','入耳式',1,'黑色','available');
INSERT INTO "product_skus" VALUES('V202-2','V202','入耳式',1,'银色','unavailable');
INSERT INTO "product_skus" VALUES('V203-1','V203','半入耳式',0,'白色','available');
INSERT INTO "product_skus" VALUES('V203-2','V203','半入耳式',0,'粉色','available');
INSERT INTO "product_skus" VALUES('V204-1','V204','头戴式',1,'黑色','available');
INSERT INTO "product_skus" VALUES('V204-2','V204','头戴式',1,'米色','available');
INSERT INTO "product_skus" VALUES('V205-1','V205','头戴式',0,'黑色','unavailable');
INSERT INTO "product_skus" VALUES('V205-2','V205','头戴式',0,'蓝色','unavailable');
INSERT INTO "product_skus" VALUES('V206-1','V206','开放式挂耳',0,'黑色','available');
INSERT INTO "product_skus" VALUES('V206-2','V206','开放式挂耳',0,'橙色','unavailable');
INSERT INTO "product_skus" VALUES('V207-1','V207','开放式耳夹',0,'银色','available');
INSERT INTO "product_skus" VALUES('V207-2','V207','开放式耳夹',0,'紫色','available');
INSERT INTO "product_skus" VALUES('V208-1','V208','骨传导挂耳',0,'黑色','available');
INSERT INTO "product_skus" VALUES('V208-2','V208','骨传导挂耳',0,'蓝色','available');
INSERT INTO "product_skus" VALUES('V209-1','V209',NULL,NULL,'黑色','available');
INSERT INTO "product_skus" VALUES('V209-2','V209',NULL,NULL,'白色','available');
INSERT INTO "product_skus" VALUES('V210-1','V210',NULL,NULL,'白色','available');
INSERT INTO "product_skus" VALUES('V210-2','V210',NULL,NULL,'蓝色','available');
INSERT INTO "product_skus" VALUES('V211-1','V211',NULL,NULL,'黑色','available');
INSERT INTO "product_skus" VALUES('V211-2','V211',NULL,NULL,'白色','unknown');
INSERT INTO "product_skus" VALUES('V212-1','V212',NULL,NULL,'黑色','available');
INSERT INTO "product_skus" VALUES('V212-2','V212',NULL,NULL,'绿色','available');
CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    brand      TEXT NOT NULL,
    price      REAL NOT NULL,
    currency   TEXT NOT NULL DEFAULT 'CNY',
    attributes TEXT NOT NULL DEFAULT ''
);
INSERT INTO "products" VALUES('V201','演示 A1 入耳耳机','无线耳机','合成演示',199.0,'CNY','入耳式；关闭降噪单次续航8小时；USB-C充电；不支持无线充电');
INSERT INTO "products" VALUES('V202','演示 A2 入耳耳机','无线耳机','合成演示',399.0,'CNY','入耳式；关闭降噪单次续航10小时；支持无线充电');
INSERT INTO "products" VALUES('V203','演示 S1 半入耳耳机','无线耳机','合成演示',149.0,'CNY','半入耳式；单次续航6小时；不支持主动降噪');
INSERT INTO "products" VALUES('V204','演示 H1 头戴耳机','无线耳机','合成演示',549.0,'CNY','头戴式；开启降噪单次续航40小时；支持3.5mm有线连接');
INSERT INTO "products" VALUES('V205','演示 H2 头戴耳机','无线耳机','合成演示',259.0,'CNY','头戴式；单次续航45小时；不支持主动降噪');
INSERT INTO "products" VALUES('V206','演示 O1 开放挂耳耳机','无线耳机','合成演示',299.0,'CNY','开放式挂耳；不进入耳道；单次续航9小时；不支持主动降噪');
INSERT INTO "products" VALUES('V207','演示 O2 耳夹耳机','无线耳机','合成演示',399.0,'CNY','开放式耳夹；不进入耳道；单次续航7小时；不支持主动降噪');
INSERT INTO "products" VALUES('V208','演示 B1 骨传导耳机','无线耳机','合成演示',499.0,'CNY','骨传导挂耳；不进入耳道；单次续航8小时；不支持主动降噪');
INSERT INTO "products" VALUES('V209','演示 C65 充电器','充电配件','合成演示',129.0,'CNY','最高65W；双USB-C口；不附赠充电线');
INSERT INTO "products" VALUES('V210','演示 C30 充电器','充电配件','合成演示',79.0,'CNY','最高30W；单USB-C口；附赠1米充电线');
INSERT INTO "products" VALUES('V211','演示 P10 充电宝','充电配件','合成演示',159.0,'CNY','10000mAh；最高20W有线输出；不支持无线充电');
INSERT INTO "products" VALUES('V212','演示 P20 充电宝','充电配件','合成演示',239.0,'CNY','20000mAh；最高45W有线输出；不支持无线充电');
CREATE TABLE return_audit (audit_id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES return_requests(request_id), action TEXT NOT NULL,actor TEXT NOT NULL, detail_json TEXT NOT NULL,created_at TEXT NOT NULL);
INSERT INTO "return_audit" VALUES('c85f92f830ee45fbbe194a5c31d26b08','R96d15f95f03b4adaa4e790ac156fe82a','created','USER001','{"preview_id": "5ed5f9a32b304a8c89c91b42f06e4c95", "revision": 1, "policy_hash": "ac2accea093f9a63849654ca840ea0cb5789f5d41ccc2cd6300671dbab35afc9", "entry": "explicit_ui_button"}','2026-10-23T01:59:18.035809+00:00');
CREATE TABLE return_confirmations (preview_id TEXT PRIMARY KEY REFERENCES return_previews(preview_id),user_id TEXT NOT NULL REFERENCES users(user_id),confirmed_at TEXT NOT NULL, consumed_at TEXT NOT NULL);
INSERT INTO "return_confirmations" VALUES('5ed5f9a32b304a8c89c91b42f06e4c95','USER001','2026-10-23T01:59:18.035809+00:00','2026-10-23T01:59:18.035809+00:00');
CREATE TABLE return_evidence (evidence_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversations(session_id), media_type TEXT NOT NULL,data BLOB NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE return_previews (preview_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES conversations(session_id), revision INTEGER NOT NULL,content_json TEXT NOT NULL, policy_hash TEXT NOT NULL,expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
INSERT INTO "return_previews" VALUES('5ed5f9a32b304a8c89c91b42f06e4c95','0a9367a9a3884116ba7a8a9b49771922',1,'{"order_id": "S20001", "item_id": 1, "product_id": "V204", "product_name": "演示 H1 头戴耳机", "sku": {"sku_id": "V204-2", "product_id": "V204", "wearing_type": "头戴式", "anc": 1, "color": "米色", "availability": "available"}, "quantity": 1, "reason": "no_reason", "condition": "sealed", "problem_description": null, "secondary_sale_ok": true, "quality_non_user_damage": null, "evidence": null, "claims_verified": false, "delivered_at": "2026-10-15", "checked_on": "2026-10-15", "policy": {"source": "return_policy.md", "version": "v1", "hash": "ac2accea093f9a63849654ca840ea0cb5789f5d41ccc2cd6300671dbab35afc9", "reason": "no_reason", "window_days": 7, "shipping_payer": "买家", "starting_point": "签收日", "required_conditions": ["商品本身、配件、说明书、包装盒齐全", "未出现明显使用痕迹、划痕、污渍", "无线耳机类商品的耳塞硅胶套未拆封使用。"], "requires_uploaded_media": false}}','ac2accea093f9a63849654ca840ea0cb5789f5d41ccc2cd6300671dbab35afc9','2026-10-23T02:09:17.597131+00:00','2026-10-23T01:59:17.597166+00:00');
CREATE TABLE return_requests (request_id TEXT PRIMARY KEY, preview_id TEXT NOT NULL UNIQUE REFERENCES return_previews(preview_id), session_id TEXT NOT NULL REFERENCES conversations(session_id), user_id TEXT NOT NULL REFERENCES users(user_id), order_id TEXT NOT NULL REFERENCES orders(order_id),item_id INTEGER NOT NULL REFERENCES order_items(id),quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0),status TEXT NOT NULL CHECK(status IN ('pending_review','approved','rejected')),content_json TEXT NOT NULL, created_at TEXT NOT NULL);
INSERT INTO "return_requests" VALUES('R96d15f95f03b4adaa4e790ac156fe82a','5ed5f9a32b304a8c89c91b42f06e4c95','0a9367a9a3884116ba7a8a9b49771922','USER001','S20001',1,1,'pending_review','{"order_id": "S20001", "item_id": 1, "product_id": "V204", "product_name": "演示 H1 头戴耳机", "sku": {"sku_id": "V204-2", "product_id": "V204", "wearing_type": "头戴式", "anc": 1, "color": "米色", "availability": "available"}, "quantity": 1, "reason": "no_reason", "condition": "sealed", "problem_description": null, "secondary_sale_ok": true, "quality_non_user_damage": null, "evidence": null, "claims_verified": false, "delivered_at": "2026-10-15", "checked_on": "2026-10-15", "policy": {"source": "return_policy.md", "version": "v1", "hash": "ac2accea093f9a63849654ca840ea0cb5789f5d41ccc2cd6300671dbab35afc9", "reason": "no_reason", "window_days": 7, "shipping_payer": "买家", "starting_point": "签收日", "required_conditions": ["商品本身、配件、说明书、包装盒齐全", "未出现明显使用痕迹、划痕、污渍", "无线耳机类商品的耳塞硅胶套未拆封使用。"], "requires_uploaded_media": false}}','2026-10-23T01:59:18.035809+00:00');
CREATE TABLE return_reviews (request_id TEXT PRIMARY KEY REFERENCES return_requests(request_id),decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')),reason TEXT NOT NULL CHECK(length(trim(reason))>0),reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL);
CREATE TABLE return_tasks (session_id TEXT PRIMARY KEY REFERENCES conversations(session_id),data_json TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO "return_tasks" VALUES('0a9367a9a3884116ba7a8a9b49771922','{"order_id": "S20001", "item_id": 1, "quantity": 1, "reason": "no_reason", "condition": "sealed", "problem_description": null, "secondary_sale_ok": true, "quality_non_user_damage": null, "sources": {"order_id": {"turn_id": "ea9f680bf0884e3494033f8eacd89898", "user_text": "[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，剩余数量需核查。", "type": "user_claim", "updated_at": "2026-10-23T01:59:17.232065+00:00"}, "item_id": {"turn_id": "ea9f680bf0884e3494033f8eacd89898", "user_text": "[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，剩余数量需核查。", "type": "user_claim", "updated_at": "2026-10-23T01:59:17.232093+00:00"}, "quantity": {"turn_id": "ea9f680bf0884e3494033f8eacd89898", "user_text": "[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，剩余数量需核查。", "type": "user_claim", "updated_at": "2026-10-23T01:59:17.232097+00:00"}, "reason": {"turn_id": "ea9f680bf0884e3494033f8eacd89898", "user_text": "[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，剩余数量需核查。", "type": "user_claim", "updated_at": "2026-10-23T01:59:17.232100+00:00"}, "condition": {"turn_id": "ea9f680bf0884e3494033f8eacd89898", "user_text": "[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，剩余数量需核查。", "type": "user_claim", "updated_at": "2026-10-23T01:59:17.232103+00:00"}, "secondary_sale_ok": {"turn_id": "ea9f680bf0884e3494033f8eacd89898", "user_text": "[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，剩余数量需核查。", "type": "user_claim", "updated_at": "2026-10-23T01:59:17.232105+00:00"}}, "revision": 1, "status": "submitted", "request_id": "R96d15f95f03b4adaa4e790ac156fe82a"}','2026-10-23T01:59:18.035809+00:00');
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
CREATE UNIQUE INDEX conversation_one_running ON conversation_turns(session_id) WHERE status='running';
CREATE TRIGGER return_quantity_insert BEFORE INSERT ON return_requests WHEN NEW.status IN ('pending_review','approved') BEGIN SELECT CASE WHEN NEW.quantity + COALESCE((SELECT SUM(quantity) FROM return_requests WHERE item_id=NEW.item_id AND status IN ('pending_review','approved') AND request_id<>NEW.request_id),0) > (SELECT quantity FROM order_items WHERE id=NEW.item_id) THEN RAISE(ABORT,'return quantity exceeded') END; END;
CREATE TRIGGER return_quantity_update BEFORE UPDATE ON return_requests WHEN NEW.status IN ('pending_review','approved') BEGIN SELECT CASE WHEN NEW.quantity + COALESCE((SELECT SUM(quantity) FROM return_requests WHERE item_id=NEW.item_id AND status IN ('pending_review','approved') AND request_id<>NEW.request_id),0) > (SELECT quantity FROM order_items WHERE id=NEW.item_id) THEN RAISE(ABORT,'return quantity exceeded') END; END;
CREATE TRIGGER return_status_transition BEFORE UPDATE OF status ON return_requests WHEN NEW.status<>OLD.status AND (OLD.status<>'pending_review' OR NEW.status NOT IN ('approved','rejected')) BEGIN SELECT RAISE(ABORT,'illegal return status transition'); END;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('price_history',24);
INSERT INTO "sqlite_sequence" VALUES('order_items',75);
COMMIT;
