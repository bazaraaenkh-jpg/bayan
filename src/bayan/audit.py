"""SQLAlchemy event listeners for automatic Audit Logging.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import event
from sqlalchemy.orm import Session, attributes

from .context import current_actor_id, current_company_id
from .models import AuditLog


def _serialize(val):
    """JSON-оор хадгалах боломжтой формат руу хөрвүүлнэ."""
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    return val


@event.listens_for(Session, "before_flush")
def audit_before_flush(session: Session, flush_context, instances):
    actor = current_actor_id.get()
    company = current_company_id.get()
    
    logs: list[AuditLog] = []
    
    # 1. Шинээр нэмэгдсэн объектууд (Inserts)
    for obj in session.new:
        if isinstance(obj, AuditLog):
            continue
        
        table = getattr(obj, "__tablename__", None)
        if not table:
            continue
            
        pk = getattr(obj, "id", None)
        new_values = {}
        for col in obj.__table__.columns:
            new_values[col.name] = _serialize(getattr(obj, col.name, None))
            
        obj_company_id = getattr(obj, "company_id", company)
        
        logs.append(AuditLog(
            company_id=obj_company_id or "system",
            actor_id=actor,
            action="insert",
            entity=table,
            entity_id=str(pk) if pk else "unknown",
            detail={"new": new_values}
        ))
        
    # 2. Өөрчлөгдсөн объектууд (Updates)
    for obj in session.dirty:
        if isinstance(obj, AuditLog):
            continue
            
        table = getattr(obj, "__tablename__", None)
        if not table:
            continue
            
        pk = getattr(obj, "id", None)
        old_values = {}
        new_values = {}
        
        state = attributes.instance_state(obj)
        for col in obj.__table__.columns:
            if col.name not in state.dict:
                continue
            history = attributes.get_history(obj, col.name)
            if history.has_changes():
                # history.deleted - хуучин утга, history.added - шинэ утга
                old_val = history.deleted[0] if history.deleted else None
                new_val = history.added[0] if history.added else None
                
                old_values[col.name] = _serialize(old_val)
                new_values[col.name] = _serialize(new_val)
                
        if old_values or new_values:
            obj_company_id = getattr(obj, "company_id", company)
            logs.append(AuditLog(
                company_id=obj_company_id or "system",
                actor_id=actor,
                action="update",
                entity=table,
                entity_id=str(pk) if pk else "unknown",
                detail={"old": old_values, "new": new_values}
            ))
            
    # 3. Устгагдсан объектууд (Deletes)
    for obj in session.deleted:
        if isinstance(obj, AuditLog):
            continue
            
        table = getattr(obj, "__tablename__", None)
        if not table:
            continue
            
        pk = getattr(obj, "id", None)
        old_values = {}
        for col in obj.__table__.columns:
            old_values[col.name] = _serialize(getattr(obj, col.name, None))
            
        obj_company_id = getattr(obj, "company_id", company)
        logs.append(AuditLog(
            company_id=obj_company_id or "system",
            actor_id=actor,
            action="delete",
            entity=table,
            entity_id=str(pk) if pk else "unknown",
            detail={"old": old_values}
        ))
        
    # Бэлдсэн логуудыг сешнд нэмнэ
    for log in logs:
        session.add(log)
