# Import all models here so they are registered with SQLModel.metadata.
# This is REQUIRED for:
#   1. Alembic autogenerate to detect all tables
#   2. SQLModel relationship resolution
#   3. alembic/env.py's `from app.models import *`

from app.models.tenant import Tenant  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.chunk import DocumentChunk  # noqa: F401
from app.models.conversation import Conversation, Message  # noqa: F401
