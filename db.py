import aiosqlite

async def init_db():
    async with aiosqlite.connect("flights.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                origin TEXT,
                destination TEXT,
                price INTEGER,
                departure_date TEXT,
                origin_airport TEXT,
                destination_airport TEXT,
                ticket_link TEXT,
                passengers INTEGER,
                is_subscription BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
        print("Database initialized")

async def add_search(chat_id: int, origin: str, destination: str, price: int, departure_date: str,
                    origin_airport: str, destination_airport: str, ticket_link: str, passengers: int,
                    is_subscription: bool = False):
    async with aiosqlite.connect("flights.db") as db:
        await db.execute("""
            INSERT INTO searches (chat_id, origin, destination, price, departure_date,
                                 origin_airport, destination_airport, ticket_link, passengers, is_subscription)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (chat_id, origin, destination, price, departure_date, origin_airport,
              destination_airport, ticket_link, passengers, is_subscription))
        await db.commit()
        print(f"Added {'subscription' if is_subscription else 'search'} for {origin}-{destination}, chat_id: {chat_id}")

async def get_all_searches():
    async with aiosqlite.connect("flights.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM searches WHERE is_subscription = 1") as cursor:
            searches = await cursor.fetchall()
            return searches

async def get_user_searches(chat_id: int):
    async with aiosqlite.connect("flights.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM searches WHERE chat_id = ? AND is_subscription = 0 ORDER BY created_at DESC",
                             (chat_id,)) as cursor:
            return await cursor.fetchall()

async def get_user_subscriptions(chat_id: int):
    async with aiosqlite.connect("flights.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM searches WHERE chat_id = ? AND is_subscription = 1 ORDER BY created_at DESC",
                             (chat_id,)) as cursor:
            return await cursor.fetchall()

async def update_price(search_id: int, price: int, departure_date: str, origin_airport: str,
                      destination_airport: str, ticket_link: str, passengers: int):
    async with aiosqlite.connect("flights.db") as db:
        await db.execute("""
            UPDATE searches
            SET price = ?, departure_date = ?, origin_airport = ?, destination_airport = ?, ticket_link = ?, passengers = ?
            WHERE id = ?
        """, (price, departure_date, origin_airport, destination_airport, ticket_link, passengers, search_id))
        await db.commit()
        print(f"Updated price for search_id {search_id} to {price}")