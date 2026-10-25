"""국립중앙의료원(NMC) 응급의료·약국·병의원 module: cached fetch, offline snapshots, 밤 의료 공백 evidence."""

from .service import MedicalService, Region, region_of

__all__ = ["MedicalService", "Region", "region_of"]
