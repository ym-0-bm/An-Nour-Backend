import logging

from prisma import Prisma

logger = logging.getLogger(__name__)

prisma = Prisma()

async def connect_db():
    """Connecter à la base de données MongoDB"""
    await prisma.connect()
    logger.info("MongoDB connecté")

async def disconnect_db():
    """Déconnecter de MongoDB"""
    await prisma.disconnect()
    logger.info("MongoDB déconnecté")
