import sqlite3
import pandas as pd

def inspect_database(db_path='parking.db'):
    conn = sqlite3.connect(db_path)
    
    # Get all tables
    tables = conn.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' 
        AND name NOT LIKE 'sqlite_%'
    """).fetchall()
    
    print("=" * 50)
    print("DATABASE INSPECTION")
    print("=" * 50)
    
    for table in tables:
        table_name = table[0]
        print(f"\n📊 TABLE: {table_name}")
        print("-" * 30)
        
        # Get column info
        columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        print("Columns:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]}) {'PK' if col[5] else ''}")
        
        # Get row count
        count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"Total records: {count}")
        
        # Show sample data
        if count > 0:
            df = pd.read_sql_query(f"SELECT * FROM {table_name} LIMIT 3", conn)
            print("Sample data:")
            print(df.to_string(index=False))
    
    conn.close()

if __name__ == "__main__":
    inspect_database()