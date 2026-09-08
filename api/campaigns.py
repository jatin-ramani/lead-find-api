"""FastAPI router for Email Campaigns and Recipients."""

from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from database.db import get_db
from errors import AppError, ErrorCode
from schemas.campaign import (
    CampaignCreate,
    CampaignFilterCriteria,
    CampaignListResponse,
    CampaignOut,
    CampaignRecipientListResponse,
    CampaignRecipientOut,
    CampaignSingleResponse,
    CampaignUpdate,
    ProcessDueCampaignsResponse,
    RecipientPreviewResponse,
)
from schemas.common import VALIDATION_ERROR_RESPONSE
from services.email_campaign_service import (
    CampaignStateError,
    cancel_campaign,
    count_campaign_recipients,
    count_campaigns,
    create_campaign,
    delete_campaign,
    get_campaign,
    list_campaign_recipients,
    list_campaigns,
    preview_eligible_recipients,
    process_due_campaigns,
    start_campaign,
    update_campaign,
)

router = APIRouter(
    prefix="/campaigns",
    tags=["Email Campaigns"],
    responses={422: VALIDATION_ERROR_RESPONSE},
)


def _serialize_campaign_out(campaign) -> dict:
    """Format campaign ORM object to match CampaignOut schema with template name."""
    filters = {}
    if campaign.filter_criteria_json:
        try:
            import json
            filters = json.loads(campaign.filter_criteria_json)
        except Exception:
            filters = {}

    return {
        "id": campaign.id,
        "name": campaign.name,
        "description": campaign.description,
        "template_id": campaign.template_id,
        "template_name": campaign.template.name if campaign.template else None,
        "status": campaign.status,
        "filter_criteria": filters,
        "recipient_count": campaign.recipient_count,
        "sent_count": campaign.sent_count,
        "failed_count": campaign.failed_count,
        "scheduled_at": campaign.scheduled_at,
        "started_at": campaign.started_at,
        "completed_at": campaign.completed_at,
        "snapshot_at": campaign.snapshot_at,
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
    }


@router.post(
    "",
    response_model=CampaignSingleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an email campaign",
    description="Create a new draft or scheduled email campaign with audience filters and a chosen template.",
)
def create_new_campaign(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
):
    try:
        filter_dict = payload.filter_criteria.model_dump(exclude_unset=True) if payload.filter_criteria else {}
        campaign = create_campaign(
            db=db,
            name=payload.name,
            template_id=payload.template_id,
            description=payload.description,
            filter_criteria=filter_dict,
            scheduled_at=payload.scheduled_at,
        )
        return CampaignSingleResponse(
            success=True,
            data=_serialize_campaign_out(campaign),
            message="Campaign created successfully",
        )
    except ValueError as exc:
        raise AppError(
            message=str(exc),
            error=ErrorCode.VALIDATION_ERROR,
            status_code=422,
        )


@router.get(
    "",
    response_model=CampaignListResponse,
    summary="List email campaigns",
    description="List all email campaigns with status filtering and pagination.",
)
def get_campaigns(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by campaign status"),
    search: Optional[str] = Query(None, description="Search by campaign name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    db: Session = Depends(get_db),
):
    items = list_campaigns(
        db,
        status=status_filter,
        search=search,
        page=page,
        page_size=page_size,
    )
    total = count_campaigns(
        db,
        status=status_filter,
        search=search,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return CampaignListResponse(
        success=True,
        items=[_serialize_campaign_out(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post(
    "/preview-recipients",
    response_model=RecipientPreviewResponse,
    summary="Preview campaign eligible recipients",
    description="Calculate the count and sample list of leads matching the campaign criteria.",
)
def preview_campaign_recipients(
    payload: CampaignFilterCriteria,
    limit: int = Query(10, ge=1, le=50, description="Number of sample leads to return"),
    db: Session = Depends(get_db),
):
    filter_dict = payload.model_dump(exclude_unset=True)
    res = preview_eligible_recipients(db, filter_dict, limit_samples=limit)
    return RecipientPreviewResponse(
        success=True,
        total_eligible_leads=res["total_eligible_leads"],
        sample_leads=res["sample_leads"],
    )


@router.post(
    "/process-due",
    response_model=ProcessDueCampaignsResponse,
    summary="Process due scheduled and running campaigns",
    description="Trigger execution for scheduled campaigns that reached their due time.",
)
def trigger_process_due_campaigns(
    limit: int = Query(10, ge=1, le=50, description="Max campaigns to process"),
    batch_size: int = Query(50, ge=1, le=200, description="Recipients batch size per campaign"),
    db: Session = Depends(get_db),
):
    result = process_due_campaigns(db, limit=limit, batch_size=batch_size)
    return ProcessDueCampaignsResponse(
        success=True,
        **result,
    )


@router.get(
    "/{campaign_id}",
    response_model=CampaignSingleResponse,
    summary="Get campaign details",
    description="Fetch a single email campaign by ID.",
)
def get_campaign_details(
    campaign_id: int,
    db: Session = Depends(get_db),
):
    campaign = get_campaign(db, campaign_id)
    if not campaign:
        raise AppError(
            message=f"Campaign with ID {campaign_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    return CampaignSingleResponse(
        success=True,
        data=_serialize_campaign_out(campaign),
    )


@router.put(
    "/{campaign_id}",
    response_model=CampaignSingleResponse,
    summary="Update campaign",
    description="Update a draft or scheduled campaign's metadata and criteria.",
)
@router.patch(
    "/{campaign_id}",
    response_model=CampaignSingleResponse,
    summary="Update campaign",
    description="Update a draft or scheduled campaign's metadata and criteria.",
)
def update_campaign_details(
    campaign_id: int,
    payload: CampaignUpdate,
    db: Session = Depends(get_db),
):
    try:
        filter_dict = payload.filter_criteria.model_dump(exclude_unset=True) if payload.filter_criteria is not None else None
        campaign = update_campaign(
            db=db,
            campaign_id=campaign_id,
            name=payload.name,
            description=payload.description,
            template_id=payload.template_id,
            filter_criteria=filter_dict,
            scheduled_at=payload.scheduled_at,
        )
        if not campaign:
            raise AppError(
                message=f"Campaign with ID {campaign_id} not found",
                error=ErrorCode.NOT_FOUND,
                status_code=404,
            )
        return CampaignSingleResponse(
            success=True,
            data=_serialize_campaign_out(campaign),
            message="Campaign updated successfully",
        )
    except CampaignStateError as exc:
        raise AppError(
            message=str(exc),
            error=ErrorCode.CONFLICT,
            status_code=409,
        )
    except ValueError as exc:
        raise AppError(
            message=str(exc),
            error=ErrorCode.VALIDATION_ERROR,
            status_code=422,
        )


@router.delete(
    "/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete campaign",
    description="Delete a draft, completed, failed or cancelled campaign.",
)
def delete_campaign_by_id(
    campaign_id: int,
    db: Session = Depends(get_db),
):
    try:
        deleted = delete_campaign(db, campaign_id)
        if not deleted:
            raise AppError(
                message=f"Campaign with ID {campaign_id} not found",
                error=ErrorCode.NOT_FOUND,
                status_code=404,
            )
    except CampaignStateError as exc:
        raise AppError(
            message=str(exc),
            error=ErrorCode.CONFLICT,
            status_code=409,
        )
    return None


@router.post(
    "/{campaign_id}/start",
    response_model=CampaignSingleResponse,
    summary="Start campaign immediately",
    description="Materialize recipients and begin campaign dispatch.",
)
def start_campaign_now(
    campaign_id: int,
    batch_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    try:
        campaign = start_campaign(db, campaign_id, batch_size=batch_size)
        return CampaignSingleResponse(
            success=True,
            data=_serialize_campaign_out(campaign),
            message="Campaign execution started successfully",
        )
    except CampaignStateError as exc:
        raise AppError(
            message=str(exc),
            error=ErrorCode.CONFLICT,
            status_code=409,
        )
    except ValueError as exc:
        raise AppError(
            message=str(exc),
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )


@router.post(
    "/{campaign_id}/cancel",
    response_model=CampaignSingleResponse,
    summary="Cancel campaign",
    description="Cancel a scheduled or running campaign.",
)
def cancel_campaign_by_id(
    campaign_id: int,
    db: Session = Depends(get_db),
):
    try:
        campaign = cancel_campaign(db, campaign_id)
        if not campaign:
            raise AppError(
                message=f"Campaign with ID {campaign_id} not found",
                error=ErrorCode.NOT_FOUND,
                status_code=404,
            )
        return CampaignSingleResponse(
            success=True,
            data=_serialize_campaign_out(campaign),
            message="Campaign cancelled successfully",
        )
    except CampaignStateError as exc:
        raise AppError(
            message=str(exc),
            error=ErrorCode.CONFLICT,
            status_code=409,
        )


@router.get(
    "/{campaign_id}/recipients",
    response_model=CampaignRecipientListResponse,
    summary="List campaign recipients",
    description="List all recipient logs and statuses for a campaign.",
)
def get_campaign_recipients(
    campaign_id: int,
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by recipient status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    db: Session = Depends(get_db),
):
    campaign = get_campaign(db, campaign_id)
    if not campaign:
        raise AppError(
            message=f"Campaign with ID {campaign_id} not found",
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )
    items = list_campaign_recipients(
        db,
        campaign_id=campaign_id,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    total = count_campaign_recipients(
        db,
        campaign_id=campaign_id,
        status=status_filter,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    recips_out = []
    for r in items:
        recips_out.append(
            CampaignRecipientOut(
                id=r.id,
                campaign_id=r.campaign_id,
                business_id=r.business_id,
                business_name=r.business.name if r.business else None,
                recipient_email=r.recipient_email,
                recipient_name=r.recipient_name,
                status=r.status,
                attempt_count=r.attempt_count,
                error_message=r.error_message,
                provider_message_id=r.provider_message_id,
                sent_at=r.sent_at,
                attempted_at=r.attempted_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )

    return CampaignRecipientListResponse(
        success=True,
        items=recips_out,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
