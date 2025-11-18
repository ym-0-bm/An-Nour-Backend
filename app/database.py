from prisma import Prisma
from contextlib import asynccontextmanager

prisma = Prisma()

async def connect_db():
    """Connecter à la base de données MongoDB"""
    await prisma.connect()
    print("✅ MongoDB connecté")

async def disconnect_db():
    """Déconnecter de MongoDB"""
    await prisma.disconnect()
    print("❌ MongoDB déconnecté")
