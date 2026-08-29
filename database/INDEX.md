<!-- AUTO-DOC: Update me when files in this folder change -->

# database

数据持久化层：封装用户/群聊主库、键值存储、消息历史、联系人、群成员与消息计数等模块，向 bot_core、utils 与插件提供统一的本地数据访问入口。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Package | 数据库包入口；导入并在导入时自动初始化 contacts_db 与 group_members_db |
| allbotDB.py | Core | 用户/群聊主库（SQLAlchemy ORM 单例）：积分、签到、白名单、LLM 线程 ID，线程池串行化写入 |
| keyvalDB.py | Core | 异步键值存储（SQLAlchemy AsyncSession 单例）：set/get/delete/exists/ttl/expire/keys + 后台过期清理 |
| messsagDB.py | Core | 异步消息历史库（inbound/outbound 双表）：保存与查询消息、定期清理 3 天前记录 |
| contacts_db.py | Store | 联系人 SQLite 库：增删改查与分页、内存缓存、导入即初始化 |
| group_members_db.py | Store | 群成员 SQLite 库：保存/查询/删除群成员、反查成员所在群、导入即初始化 |
| message_counter.py | Stats | 基于 SQLite 的消息计数器：按时段/按日统计总量、今日/昨日与增长率 |
| MessageCounter.py | Stats | 基于 JSON 文件的消息计数器单例：按平台/按日统计（与 message_counter.py 并存） |
