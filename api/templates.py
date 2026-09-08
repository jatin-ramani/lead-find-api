"""FastAPI router for Email Templates."""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from database.db import get_db
from errors import AppError, ErrorCode
from schemas.automation import SupportedVariableResponse
from schemas.common import VALIDATION_ERROR_RESPONSE
from schemas.template import (
    TemplateArchiveToggleRequest,
    TemplateCreate,
    TemplateListResponse,
    TemplateOut,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
    TemplateSingleResponse,
    TemplateUpdate,
)
from services.email_template_service import (
    TemplateInUseError,
    count_templates,
    create_template,
    delete_template,
    get_template,
    list_templates,
    preview_template_content,
    toggle_template_archive,
    update_template,
)
from services.template_engine import get_supported_variables

router = APIRouter(
    prefix="/templates",
    tags=["Email Templates"],
    responses={422: VALIDATION_ERROR_RESPONSE},
)


@router.get(
    "/variables",
    response_model=List[SupportedVariableResponse],
    summary="List supported template variables",
    description="Return allowlisted template variables available for email templates.",
)
def get_template_variables():
    return get_supported_variables()


@router.post(
    "",
    response_model=TemplateSingleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an email template",
    description="Create a new reusable email template with subject and body templates.",
)
def create_new_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
):
    template = create_template(
        db=db,
        name=payload.name,
        subject=payload.subject,
        body=payload.body,
        description=payload.description,
    )
    return TemplateSingleResponse(
        success=True,
        data=template,
        message="Template created successfully",
    )


@router.get(
    "",
    response_model=TemplateListResponse,
    summary="List email templates",
    description="List all email templates with optional search and archive status filtering.",
)
def get_templates(
    search: Optional[str] = Query(None, description="Search by template name or subject"),
    is_archived: Optional[bool] = Query(None, description="Filter by archived status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    db: Session = Depends(get_db),
):
    items = list_templates(
        db,
        search=search,
        is_archived=is_archived,
        page=page,
        page_size=page_size,
    )
    total = count_templates(
        db,
        search=search,
        is_archived=is_archived,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return TemplateListResponse(
        success=True,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post(
    "/preview",
    response_model=TemplatePreviewResponse,
    summary="Preview template rendering",
    description="Render a template subject and body using sample or real lead context.",
)
def preview_template(
    payload: TemplatePreviewRequest,
    db: Session = Depends(get_db),
):
    result = preview_template_content(
        db=db,
        subject=payload.subject,
        body=payload.body,
        template_id=payload.template_id,
        business_id=payload.business_id,
        custom_context=payload.custom_context,
    )
    return TemplatePreviewResponse(
        success=True,
        **result,
    )


@router.get(
    "/{template_id}",
    response_model=TemplateSingleResponse,
    summary="Get template details",
    description="Fetch a single email template by ID.",
)
def get_template_details(
    template_id: int,
    db: Session = Depends(get_db),
):
    template = get_template(db, template_id)
    if not template:
        raise AppError(
            message=f"Template with ID {template_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return TemplateSingleResponse(
        success=True,
        data=template,
    )


@router.put(
    "/{template_id}",
    response_model=TemplateSingleResponse,
    summary="Update template",
    description="Update an existing email template's attributes.",
)
@router.patch(
    "/{template_id}",
    response_model=TemplateSingleResponse,
    summary="Update template",
    description="Update an existing email template's attributes.",
)
def update_template_details(
    template_id: int,
    payload: TemplateUpdate,
    db: Session = Depends(get_db),
):
    template = update_template(
        db=db,
        template_id=template_id,
        name=payload.name,
        description=payload.description,
        subject=payload.subject,
        body=payload.body,
        is_archived=payload.is_archived,
    )
    if not template:
        raise AppError(
            message=f"Template with ID {template_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return TemplateSingleResponse(
        success=True,
        data=template,
        message="Template updated successfully",
    )


@router.post(
    "/{template_id}/archive",
    response_model=TemplateSingleResponse,
    summary="Toggle template archive state",
    description="Archive or unarchive an email template.",
)
def toggle_template_archive_state(
    template_id: int,
    payload: TemplateArchiveToggleRequest,
    db: Session = Depends(get_db),
):
    template = toggle_template_archive(db, template_id, payload.is_archived)
    if not template:
        raise AppError(
            message=f"Template with ID {template_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    action = "archived" if payload.is_archived else "unarchived"
    return TemplateSingleResponse(
        success=True,
        data=template,
        message=f"Template {action} successfully",
    )


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete template",
    description="Delete an email template. Fails with 409 if referenced by any campaigns.",
)
def delete_template_by_id(
    template_id: int,
    db: Session = Depends(get_db),
):
    try:
        deleted = delete_template(db, template_id)
        if not deleted:
            raise AppError(
                message=f"Template with ID {template_id} not found",
                error=ErrorCode.NOT_FOUND,
                status_code=404,
            )
    except TemplateInUseError as exc:
        raise AppError(
            message=str(exc),
            error=ErrorCode.CONFLICT,
            status_code=409,
        )
    return None
