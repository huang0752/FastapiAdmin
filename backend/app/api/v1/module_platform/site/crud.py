from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import SiteModel
from .schema import SiteCreateSchema, SiteUpdateSchema


class SiteCRUD(CRUDBase[SiteModel, SiteCreateSchema, SiteUpdateSchema]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SiteModel, auth=auth)
