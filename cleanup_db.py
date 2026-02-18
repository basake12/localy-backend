"""
Database Cleanup Script - Run this before migrations
This will clean up partial migration state automatically
"""
import psycopg2
from psycopg2 import sql
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get database URL from environment
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in environment variables")
    print("Please set it in your .env file")
    exit(1)


def cleanup_database():
    """Clean up partial migration state"""
    try:
        # Connect to database
        print("Connecting to database...")
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        cur = conn.cursor()

        print("\n=== Starting Database Cleanup ===\n")

        # 1. Drop partial indexes
        print("Step 1: Dropping partial indexes...")
        indexes_to_drop = [
            'idx_delivery_zones_center_location',
            'idx_pharmacy_orders_delivery_location',
            'idx_riders_current_location',
            'idx_search_queries_location',
            'idx_customer_profiles_current_location',
            'idx_customer_profiles_default_location',
            'idx_businesses_location',
            'idx_product_orders_shipping_location'
        ]

        for index_name in indexes_to_drop:
            try:
                cur.execute(f"DROP INDEX IF EXISTS {index_name} CASCADE")
                print(f"  ✓ Dropped index: {index_name}")
            except Exception as e:
                print(f"  ⚠ Warning dropping {index_name}: {e}")

        # 2. Drop partial tables
        print("\nStep 2: Dropping partial tables...")
        tables_to_drop = [
            'pharmacy_orders',
            'delivery_zones',
            'daily_analytics_snapshots',
            'prescriptions',
            'rider_shifts',
            'rider_earnings',
            'delivery_tracking',
            'deliveries'
        ]

        for table_name in tables_to_drop:
            try:
                cur.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
                print(f"  ✓ Dropped table: {table_name}")
            except Exception as e:
                print(f"  ⚠ Warning dropping {table_name}: {e}")

        # 3. Clear failed migration from alembic_version
        print("\nStep 3: Clearing failed migration record...")
        try:
            cur.execute("DELETE FROM alembic_version WHERE version_num = '342631e63bcc'")
            print("  ✓ Cleared migration version 342631e63bcc")
        except Exception as e:
            print(f"  ⚠ Warning clearing alembic_version: {e}")

        # 4. Enable PostGIS extension
        print("\nStep 4: Enabling PostGIS extension...")
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            print("  ✓ PostGIS extension enabled")
        except Exception as e:
            print(f"  ✗ ERROR enabling PostGIS: {e}")
            print("  → You may need to install PostGIS on your PostgreSQL server")
            print("  → Or run this as a superuser")

        # 5. Verify PostGIS
        print("\nStep 5: Verifying PostGIS installation...")
        try:
            cur.execute("SELECT postgis_version()")
            version = cur.fetchone()[0]
            print(f"  ✓ PostGIS version: {version}")
        except Exception as e:
            print(f"  ✗ ERROR: PostGIS not available: {e}")
            print("  → Please install PostGIS before running migrations")

        # 6. Check final state
        print("\nStep 6: Checking database state...")

        # Check alembic_version
        cur.execute("SELECT COUNT(*) FROM alembic_version")
        count = cur.fetchone()[0]
        print(f"  • Alembic versions: {count} (should be 0 for fresh start)")

        # Check for any remaining delivery-related tables
        cur.execute("""
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public'
                      AND tablename LIKE '%delivery%'
                    """)
        tables = cur.fetchall()
        if tables:
            print(f"  ⚠ Remaining delivery tables: {[t[0] for t in tables]}")
        else:
            print("  ✓ No delivery tables found (clean state)")

        cur.close()
        conn.close()

        print("\n=== Cleanup Complete! ===")
        print("\nYou can now run: alembic upgrade head")

    except psycopg2.OperationalError as e:
        print(f"\n✗ ERROR connecting to database: {e}")
        print("\nPlease check:")
        print("1. PostgreSQL is running")
        print("2. DATABASE_URL is correct in .env")
        print("3. Database exists")
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    cleanup_database()