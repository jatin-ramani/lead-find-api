from database.db import engine, Base
from database.models import Business

print("Creating database...")

Base.metadata.create_all(bind=engine)

print("Database created successfully!")