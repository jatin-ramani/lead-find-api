"""FastAPI router for Email Automations and Execution Logs."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.orm import Session

from database.db import get_db
from errors import AppError, ErrorCode
from schemas.common import VALIDATION_ERROR_RESPONSE
from schemas.automation import (
    AIGenerateTemplatesRequest,
    AIGradeTemplatesResponse,
    AISingleTemplateRequest,
    AISingleTemplateResponse,
    AutomationCreate,
    AutomationExecutionOut,
    AutomationListResponse,
    AutomationOut,
    AutomationSingleResponse,
    AutomationToggleRequest,
    AutomationUpdate,
    CityAutomationListResponse,
    CityAutomationReportResponse,
    CityAutomationStartRequest,
    CityGradeStatsResponse,
    CityStatListResponse,
    ExecutionListResponse,
    MasterTemplateItem,
    MasterTemplateResponse,
    ProcessDueResponse,
    SupportedVariableResponse,
)
from services.city_automation_service import (
    cancel_city_automation,
    generate_city_grade_templates,
    generate_single_city_template,
    get_available_cities,
    get_city_automation_report,
    get_city_lead_grade_stats,
    get_master_cold_email_template,
    list_city_automations,
    resume_city_automation,
    start_city_automation,
)
from services.email_queue_worker import process_campaign_queue
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


# ============================================================================
# City-First Grade Automation Endpoints
# ============================================================================

@router.get(
    "/cities",
    response_model=CityStatListResponse,
    summary="List available cities and lead counts",
    description="Return distinct cities from user business leads with total and email-eligible counts.",
)
def get_cities_list(db: Session = Depends(get_db)):
    cities = get_available_cities(db)
    return CityStatListResponse(success=True, items=cities)


@router.get(
    "/city-stats",
    response_model=CityGradeStatsResponse,
    summary="Get city lead counts and grade breakdown",
    description="Calculate grade distribution (A, B, C, D) and email eligibility for a given city.",
)
def get_city_stats(
    city: str = Query(..., min_length=1, description="City name to query"),
    db: Session = Depends(get_db),
):
    stats = get_city_lead_grade_stats(db, city)
    return CityGradeStatsResponse(success=True, **stats)


@router.get(
    "/master-template",
    response_model=MasterTemplateResponse,
    summary="Get universal master cold email template",
    description="Return the universal master cold email template for website mockup outreach.",
)
def get_master_template_endpoint(
    city: Optional[str] = Query(None, description="Optional city name for template context"),
):
    tpl = get_master_cold_email_template(city=city)
    return MasterTemplateResponse(
        success=True,
        city=city,
        data=MasterTemplateItem(
            name=tpl.get("name", "Universal Master Cold Email — Website Mockup"),
            subject=tpl["subject"],
            body=tpl["body"],
        ),
    )


@router.post(
    "/generate-templates",
    response_model=AIGradeTemplatesResponse,
    summary="Generate AI email templates for Grades A, B, C, D",
    description="Generate tailored outreach templates for all 4 lead grades in a specific city using AI.",
)
def generate_ai_templates(payload: AIGenerateTemplatesRequest):
    templates = generate_city_grade_templates(
        city=payload.city,
        industry=payload.industry,
    )
    return AIGradeTemplatesResponse(
        success=True,
        city=payload.city,
        data=templates,
    )


@router.post(
    "/generate-single-template",
    response_model=AISingleTemplateResponse,
    summary="Regenerate a single grade email template",
    description="Regenerate a single grade outreach template for a specific city.",
)
def generate_single_template(payload: AISingleTemplateRequest):
    grade_norm = payload.grade.upper().strip()
    if grade_norm not in {"A", "B", "C", "D"}:
        raise AppError(
            message=f"Invalid grade '{payload.grade}'. Must be one of A, B, C, D.",
            error=ErrorCode.VALIDATION_ERROR,
            status_code=422,
        )
    tpl = generate_single_city_template(
        grade=grade_norm,
        city=payload.city,
        industry=payload.industry,
    )
    return AISingleTemplateResponse(
        success=True,
        grade=grade_norm,
        data=tpl,
    )


@router.post(
    "/start-city-automation",
    response_model=CityAutomationReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start city-first email automation with universal master cold email",
    description="Snapshot email-eligible leads in city, assign universal master cold email, and dispatch emails in background.",
)
def launch_city_automation(
    payload: CityAutomationStartRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    tpl_dict = None
    if payload.template:
        tpl_dict = {
            "subject": payload.template.subject,
            "body": payload.template.body,
            "name": payload.template.name or f"Email Automation — {payload.city} — Master Template",
        }

    tpls_dict = None
    if payload.templates:
        tpls_dict = {
            grade: {
                "subject": item.subject,
                "body": item.body,
                "name": item.name,
            }
            for grade, item in payload.templates.items()
        }

    try:
        report = start_city_automation(
            db=db,
            city=payload.city,
            template=tpl_dict,
            templates=tpls_dict,
            name=payload.name,
            scheduled_at=payload.scheduled_at,
            execute_now=True,
        )
        if report and report.get("id") and report.get("status") == "running":
            background_tasks.add_task(process_campaign_queue, report["id"])

        return CityAutomationReportResponse(
            success=True,
            data=report,
            message=f"Automation successfully launched for {payload.city}.",
        )
    except ValueError as e:
        raise AppError(
            message=str(e),
            error=ErrorCode.VALIDATION_ERROR,
            status_code=400,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise e


@router.get(
    "/runs",
    response_model=CityAutomationListResponse,
    summary="List past city automation runs",
    description="List historical city automation runs with summary counts.",
)
def get_automation_runs(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    db: Session = Depends(get_db),
):
    result = list_city_automations(db, page=page, page_size=page_size)
    return CityAutomationListResponse(
        success=True,
        **result,
    )


@router.get(
    "/runs/{campaign_id}",
    response_model=CityAutomationReportResponse,
    summary="Get automation run report and logs",
    description="Fetch live progress, grade breakdown metrics, and recipient execution logs for an automation run.",
)
def get_automation_run_report(
    campaign_id: int,
    db: Session = Depends(get_db),
):
    try:
        report = get_city_automation_report(db, campaign_id)
        return CityAutomationReportResponse(
            success=True,
            data=report,
        )
    except ValueError as e:
        raise AppError(
            message=str(e),
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )


@router.post(
    "/runs/{campaign_id}/resume",
    response_model=CityAutomationReportResponse,
    summary="Resume paused automation run",
    description="Resume a paused automation run and continue queue processing.",
)
def resume_automation_run(
    campaign_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        report = resume_city_automation(db, campaign_id)
        background_tasks.add_task(process_campaign_queue, campaign_id)
        return CityAutomationReportResponse(
            success=True,
            data=report,
            message="Automation run resumed successfully.",
        )
    except ValueError as e:
        raise AppError(
            message=str(e),
            error=ErrorCode.NOT_FOUND,
            status_code=404,
        )


@router.post(
    "/runs/{campaign_id}/cancel",
    response_model=CityAutomationReportResponse,
    summary="Cancel active automation run",
    description="Cancel pending recipients for an active or scheduled automation run.",
)
def cancel_automation_run(
    campaign_id: int,
    db: Session = Depends(get_db),
):
    try:
        report = cancel_city_automation(db, campaign_id)
        return CityAutomationReportResponse(
            success=True,
            data=report,
            message="Automation run cancelled successfully.",
        )
    except ValueError as e:
        raise AppError(
            message=str(e),
            error=ErrorCode.NOT_FOUND,
            status_code=404,
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
