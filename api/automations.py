"""FastAPI router for Email Automations and Execution Logs."""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from database.db import get_db
from errors import AppError, ErrorCode
from schemas.common import VALIDATION_ERROR_RESPONSE
from schemas.automation import (
    AutomationCreate,
    AutomationExecutionOut,
    AutomationListResponse,
    AutomationOut,
    AutomationSingleResponse,
    AutomationToggleRequest,
    AutomationUpdate,
    ExecutionListResponse,
    ProcessDueResponse,
    SupportedVariableResponse,
)
from services.email_automation_service import (
    count_automations,
    count_executions,
    create_automation,
    delete_automation,
    get_automation,
    list_automations,
    list_executions,
    process_due_executions,
    toggle_automation_enabled,
    update_automation,
)
from services.template_engine import get_supported_variables

router = APIRouter(
    prefix="/automations",
    tags=["Email Automations"],
    responses={422: VALIDATION_ERROR_RESPONSE},
)


@router.get(
    "/variables",
    response_model=List[SupportedVariableResponse],
    summary="List supported template variables",
    description="Return allowlisted template variables available for automation emails.",
)
def get_template_variables():
    return get_supported_variables()


@router.post(
    "",
    response_model=AutomationSingleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an email automation",
    description="Create a new email automation rule triggered by CRM events.",
)
def create_new_automation(
    payload: AutomationCreate,
    db: Session = Depends(get_db),
):
    automation = create_automation(
        db=db,
        name=payload.name,
        trigger_type=payload.trigger_type,
        subject_template=payload.subject_template,
        body_template=payload.body_template,
        description=payload.description,
        enabled=payload.enabled,
        delay_minutes=payload.delay_minutes,
        max_retries=payload.max_retries,
    )
    return AutomationSingleResponse(
        success=True,
        data=automation,
        message="Automation created successfully",
    )


@router.get(
    "",
    response_model=AutomationListResponse,
    summary="List email automations",
    description="List all email automations with optional filtering by status and trigger type.",
)
def get_automations(
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    trigger_type: Optional[str] = Query(None, description="Filter by trigger type"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    db: Session = Depends(get_db),
):
    items = list_automations(
        db,
        trigger_type=trigger_type,
        enabled=enabled,
        page=page,
        page_size=page_size,
    )
    total = count_automations(
        db,
        trigger_type=trigger_type,
        enabled=enabled,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return AutomationListResponse(
        success=True,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/executions",
    response_model=ExecutionListResponse,
    summary="List all email automation executions",
    description="List execution history across all automations or for a specific lead.",
)
def get_all_executions(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    automation_id: Optional[int] = Query(None, description="Filter by automation ID"),
    business_id: Optional[int] = Query(None, description="Filter by business ID"),
    status: Optional[str] = Query(None, description="Filter by execution status"),
    db: Session = Depends(get_db),
):
    items = list_executions(
        db,
        automation_id=automation_id,
        business_id=business_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    total = count_executions(
        db,
        automation_id=automation_id,
        business_id=business_id,
        status=status,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return ExecutionListResponse(
        success=True,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post(
    "/process-due",
    response_model=ProcessDueResponse,
    summary="Process due email executions",
    description="Trigger processing of scheduled email executions that are due or ready for retry.",
)
def trigger_process_due_executions(
    limit: int = Query(50, ge=1, le=500, description="Max batch size to process"),
    db: Session = Depends(get_db),
):
    result = process_due_executions(db, limit=limit)
    return ProcessDueResponse(
        success=True,
        **result,
    )


@router.get(
    "/{automation_id}",
    response_model=AutomationSingleResponse,
    summary="Get automation details",
    description="Get detailed configuration for a single email automation.",
)
def get_automation_details(
    automation_id: int,
    db: Session = Depends(get_db),
):
    automation = get_automation(db, automation_id)
    if not automation:
        raise AppError(
            message=f"Automation with ID {automation_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return AutomationSingleResponse(
        success=True,
        data=automation,
    )


@router.patch(
    "/{automation_id}",
    response_model=AutomationSingleResponse,
    summary="Update automation",
    description="Update fields of an existing email automation.",
)
def update_automation_details(
    automation_id: int,
    payload: AutomationUpdate,
    db: Session = Depends(get_db),
):
    automation = update_automation(
        db=db,
        automation_id=automation_id,
        name=payload.name,
        description=payload.description,
        enabled=payload.enabled,
        trigger_type=payload.trigger_type,
        subject_template=payload.subject_template,
        body_template=payload.body_template,
        delay_minutes=payload.delay_minutes,
        max_retries=payload.max_retries,
    )
    if not automation:
        raise AppError(
            message=f"Automation with ID {automation_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return AutomationSingleResponse(
        success=True,
        data=automation,
        message="Automation updated successfully",
    )


@router.post(
    "/{automation_id}/toggle",
    response_model=AutomationSingleResponse,
    summary="Toggle automation active state",
    description="Activate or deactivate an email automation.",
)
def toggle_automation_status(
    automation_id: int,
    payload: AutomationToggleRequest,
    db: Session = Depends(get_db),
):
    automation = toggle_automation_enabled(db, automation_id, payload.enabled)
    if not automation:
        raise AppError(
            message=f"Automation with ID {automation_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return AutomationSingleResponse(
        success=True,
        data=automation,
        message=f"Automation {'activated' if payload.enabled else 'deactivated'} successfully",
    )


@router.delete(
    "/{automation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete automation",
    description="Delete an email automation and all its associated scheduled executions.",
)
def delete_automation_by_id(
    automation_id: int,
    db: Session = Depends(get_db),
):
    deleted = delete_automation(db, automation_id)
    if not deleted:
        raise AppError(
            message=f"Automation with ID {automation_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return None


@router.get(
    "/{automation_id}/executions",
    response_model=ExecutionListResponse,
    summary="Get execution logs for automation",
    description="Get execution history for a specific email automation.",
)
def get_automation_executions(
    automation_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    status: Optional[str] = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
):
    automation = get_automation(db, automation_id)
    if not automation:
        raise AppError(
            message=f"Automation with ID {automation_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    items = list_executions(
        db,
        automation_id=automation_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    total = count_executions(
        db,
        automation_id=automation_id,
        status=status,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return ExecutionListResponse(
        success=True,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
