from abc import ABC, abstractmethod
from typing import Dict, Any
from pydantic import BaseModel

class DataAsset(BaseModel, ABC):
    target_id: str
    metadata: Dict[str, Any]
    
    @abstractmethod
    def dump_data(self) -> str:
        """
        Génère une représentation textuelle (tronquée, formatée, etc.) 
        des données liées à cet asset.
        """
        pass
