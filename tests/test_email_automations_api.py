"""Integration tests for Email Automations API, trigger hooks, execution, and providers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from database.models import Business, BusinessActivity, BusinessFollowUp, EmailAutomation, EmailAutomationExecution
from database.crud import save_business
from providers.email_provider import BaseEmailProvider, EmailSendResult, MockEmailProvider, ResendEmailProvider, get_email_provider
from services.email_automation_service import (
    EXEC_STATUS_FAILED,
    EXEC_STATUS_SCHEDULED,
    EXEC_STATUS_SENT,
    TRIGGER_FOLLOW_UP_DUE,
    TRIGGER_LEAD_CREATED,
    TRIGGER_LEAD_STATUS_CHANGED,
    create_automation,
    evaluate_automations_for_event,
    process_due_executions,
)
from services.follow_up_service import create_follow_up
from services.lead_status_service import update_business_lead_status


class TestEmailAutomationAPI:
    def test_unauthenticated_requests_rejected(self, unauth_client):
        assert unauth_client.get("/automations").status_code == 401
        assert unauth_client.post("/automations", json={}).status_code == 401
        assert unauth_client.get("/automations/variables").status_code == 401
        assert unauth_client.get("/automations/executions").status_code == 401
        assert unauth_client.post("/automations/process-due").status_code == 401
        assert unauth_client.get("/automations/1").status_code == 401
        assert unauth_client.patch("/automations/1", json={}).status_code == 401
        assert unauth_client.delete("/automations/1").status_code == 401
        assert unauth_client.post("/automations/1/toggle", json={"enabled": False}).status_code == 401

    def test_get_template_variables(self, client):
        res = client.get("/automations/variables")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        var_names = [item["key"] for item in data]
        assert "business_name" in var_names
        assert "contact_name" in var_names
        assert "email" in var_names
        assert "lead_status" in var_names
        assert "lead_score" in var_names

    def test_create_automation_validation_errors(self, client):
        # Empty name
        res = client.post(
            "/automations",
            json={
                "name": "   ",
                "trigger_type": "lead_created",
                "subject_template": "Hello {{business_name}}",
                "body_template": "Welcome!",
            },
        )
        assert res.status_code == 422

        # Invalid trigger type
        res = client.post(
            "/automations",
            json={
                "name": "Test",
                "trigger_type": "unknown_trigger",
                "subject_template": "Hello",
                "body_template": "Welcome!",
            },
        )
        assert res.status_code == 422

        # Negative delay
        res = client.post(
            "/automations",
            json={
                "name": "Test",
                "trigger_type": "lead_created",
                "subject_template": "Hello",
                "body_template": "Welcome!",
                "delay_minutes": -5,
            },
        )
        assert res.status_code == 422

    def test_create_and_get_automation(self, client, db):
        res = client.post(
            "/automations",
            json={
                "name": "Welcome New Leads",
                "description": "Send welcome email on lead creation",
                "trigger_type": "lead_created",
                "subject_template": "Welcome {{business_name}} to our platform",
                "body_template": "Hi {{contact_name}}, thanks for connecting with {{business_name}}.",
                "enabled": True,
                "delay_minutes": 15,
                "max_retries": 3,
            },
        )
        assert res.status_code == 201
        payload = res.json()
        assert payload["success"] is True
        auto_id = payload["data"]["id"]
        assert payload["data"]["name"] == "Welcome New Leads"
        assert payload["data"]["delay_minutes"] == 15

        # Get single
        get_res = client.get(f"/automations/{auto_id}")
        assert get_res.status_code == 200
        assert get_res.json()["data"]["name"] == "Welcome New Leads"

    def test_get_automation_not_found(self, client):
        res = client.get("/automations/999999")
        assert res.status_code == 404
        assert res.json()["error"] == "NOT_FOUND"

    def test_list_automations_and_filtering(self, client, db):
        # Create multiple automations
        create_automation(
            db=db,
            name="Auto 1",
            trigger_type="lead_created",
            subject_template="Sub 1",
            body_template="Body 1",
            enabled=True,
        )
        create_automation(
            db=db,
            name="Auto 2",
            trigger_type="lead_status_changed",
            subject_template="Sub 2",
            body_template="Body 2",
            enabled=False,
        )

        res = client.get("/automations")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 2

        # Filter by trigger_type
        res_filter = client.get("/automations?trigger_type=lead_status_changed")
        assert res_filter.status_code == 200
        items = res_filter.json()["items"]
        assert all(it["trigger_type"] == "lead_status_changed" for it in items)

        # Filter by enabled
        res_enabled = client.get("/automations?enabled=false")
        assert res_enabled.status_code == 200
        items_disabled = res_enabled.json()["items"]
        assert all(it["enabled"] is False for it in items_disabled)

    def test_update_automation(self, client, db):
        auto = create_automation(
            db=db,
            name="Original Name",
            trigger_type="lead_created",
            subject_template="Original Sub",
            body_template="Original Body",
        )

        res = client.patch(
            f"/automations/{auto.id}",
            json={
                "name": "Updated Name",
                "delay_minutes": 30,
            },
        )
        assert res.status_code == 200
        assert res.json()["data"]["name"] == "Updated Name"
        assert res.json()["data"]["delay_minutes"] == 30

    def test_toggle_automation(self, client, db):
        auto = create_automation(
            db=db,
            name="Toggle Test",
            trigger_type="lead_created",
            subject_template="Sub",
            body_template="Body",
            enabled=True,
        )

        res = client.post(
            f"/automations/{auto.id}/toggle",
            json={"enabled": False},
        )
        assert res.status_code == 200
        assert res.json()["data"]["enabled"] is False

    def test_delete_automation_and_cascade(self, client, db):
        auto = create_automation(
            db=db,
            name="To Delete",
            trigger_type="lead_created",
            subject_template="Sub",
            body_template="Body",
        )
        biz = Business(name="Cascade Biz", email="cascade@example.com")
        db.add(biz)
        db.commit()

        # Add execution
        execution = EmailAutomationExecution(
            automation_id=auto.id,
            business_id=biz.id,
            trigger_event="lead_created",
            trigger_key=f"test_cascade_{auto.id}_{biz.id}",
            status="scheduled",
            recipient_email="cascade@example.com",
            subject="Sub",
            body_rendered="Body",
            scheduled_at=datetime.now(timezone.utc),
        )
        db.add(execution)
        db.commit()

        res = client.delete(f"/automations/{auto.id}")
        assert res.status_code == 204

        # Verify automation and execution deleted
        assert db.query(EmailAutomation).filter(EmailAutomation.id == auto.id).first() is None
        assert db.query(EmailAutomationExecution).filter(EmailAutomationExecution.automation_id == auto.id).first() is None


class TestAutomationTriggersAndExecutions:
    def test_lead_status_changed_triggers_execution(self, db):
        # Create active automation for status changed
        auto = create_automation(
            db=db,
            name="Contacted Pitch",
            trigger_type=TRIGGER_LEAD_STATUS_CHANGED,
            subject_template="Following up with {{business_name}}",
            body_template="Hi, noticed your status is now {{lead_status}}.",
            enabled=True,
            delay_minutes=0,
        )

        biz = Business(name="Dental Clinic", email="clinic@example.com", lead_status="new")
        db.add(biz)
        db.commit()

        # Trigger status update
        update_business_lead_status(db, biz.id, "contacted")

        # Check execution created
        executions = db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.automation_id == auto.id,
            EmailAutomationExecution.business_id == biz.id,
        ).all()
        assert len(executions) == 1
        exec_item = executions[0]
        assert exec_item.status == EXEC_STATUS_SCHEDULED
        assert exec_item.recipient_email == "clinic@example.com"
        assert exec_item.subject == "Following up with Dental Clinic"
        assert "status is now contacted" in exec_item.body_rendered

    def test_follow_up_due_triggers_execution(self, db):
        auto = create_automation(
            db=db,
            name="Follow Up Reminder",
            trigger_type=TRIGGER_FOLLOW_UP_DUE,
            subject_template="Reminder: {{follow_up_title}} for {{business_name}}",
            body_template="Don't forget your follow-up scheduled for {{business_name}}.",
            enabled=True,
            delay_minutes=0,
        )

        biz = Business(name="Law Firm LLC", email="law@example.com")
        db.add(biz)
        db.commit()

        due_time = datetime.now(timezone.utc) + timedelta(hours=2)
        fu = create_follow_up(
            db=db,
            business_id=biz.id,
            title="Send Proposal",
            due_at=due_time,
        )

        executions = db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.automation_id == auto.id,
            EmailAutomationExecution.business_id == biz.id,
            EmailAutomationExecution.follow_up_id == fu.id,
        ).all()
        assert len(executions) == 1
        assert "Reminder: Send Proposal for Law Firm LLC" in executions[0].subject

    def test_lead_created_triggers_execution(self, db):
        auto = create_automation(
            db=db,
            name="New Lead Welcome",
            trigger_type=TRIGGER_LEAD_CREATED,
            subject_template="Welcome {{business_name}}!",
            body_template="Hello {{business_name}}, welcome to our system.",
            enabled=True,
            delay_minutes=5,
        )

        saved = save_business(
            db=db,
            name="Cafe Solar",
            phone="123-456",
            email="solar@example.com",
            website="https://solar.example.com",
            city="Ahmedabad",
            category="catering",
            address="Solar St",
            status="active",
            place_id="place_solar_1",
        )
        assert saved is True

        biz = db.query(Business).filter(Business.place_id == "place_solar_1").first()
        assert biz is not None

        executions = db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.automation_id == auto.id,
            EmailAutomationExecution.business_id == biz.id,
        ).all()
        assert len(executions) == 1
        assert executions[0].recipient_email == "solar@example.com"
        assert executions[0].subject == "Welcome Cafe Solar!"

    def test_idempotency_prevents_duplicate_executions(self, db):
        auto = create_automation(
            db=db,
            name="Idempotent Auto",
            trigger_type=TRIGGER_LEAD_CREATED,
            subject_template="Welcome",
            body_template="Welcome",
            enabled=True,
        )
        biz = Business(name="Idempotent Biz", email="idem@example.com")
        db.add(biz)
        db.commit()

        # Call evaluate multiple times
        execs1 = evaluate_automations_for_event(db, TRIGGER_LEAD_CREATED, biz.id)
        execs2 = evaluate_automations_for_event(db, TRIGGER_LEAD_CREATED, biz.id)

        assert len(execs1) == 1
        assert len(execs2) == 0  # Deduplicated

        total_execs = db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.automation_id == auto.id,
            EmailAutomationExecution.business_id == biz.id,
        ).count()
        assert total_execs == 1

    def test_batch_execution_with_mock_provider(self, db):
        provider = MockEmailProvider()
        provider.clear()

        auto = create_automation(
            db=db,
            name="Mock Auto",
            trigger_type=TRIGGER_LEAD_CREATED,
            subject_template="Outbox Test {{business_name}}",
            body_template="Body test",
            enabled=True,
            delay_minutes=0,
        )
        biz = Business(name="Mock Biz", email="mock@example.com")
        db.add(biz)
        db.commit()

        evaluate_automations_for_event(db, TRIGGER_LEAD_CREATED, biz.id)

        with patch("services.email_automation_service.get_email_provider", return_value=provider):
            result = process_due_executions(db, limit=50)

        assert result["processed"] == 1
        assert result["sent"] == 1
        assert len(provider.get_outbox()) == 1
        sent_msg = provider.get_outbox()[0]
        assert sent_msg["to_email"] == "mock@example.com"
        assert sent_msg["subject"] == "Outbox Test Mock Biz"

        # Check DB status
        exec_record = db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.automation_id == auto.id,
        ).first()
        assert exec_record.status == EXEC_STATUS_SENT
        assert exec_record.sent_at is not None

        # Verify activity logged
        act = db.query(BusinessActivity).filter(
            BusinessActivity.business_id == biz.id,
            BusinessActivity.activity_type == "email_sent",
        ).first()
        assert act is not None
        assert "Email sent" in act.title

    def test_batch_execution_transient_error_backoff(self, db):
        mock_provider = MagicMock(spec=BaseEmailProvider)
        mock_provider.name = "mock"
        mock_provider.send_email.return_value = EmailSendResult(
            success=False,
            error="Rate limit exceeded (HTTP 429)",
            is_transient=True,
        )

        auto = create_automation(
            db=db,
            name="Transient Test",
            trigger_type=TRIGGER_LEAD_CREATED,
            subject_template="Test",
            body_template="Test",
            enabled=True,
            delay_minutes=0,
            max_retries=3,
        )
        biz = Business(name="Transient Biz", email="transient@example.com")
        db.add(biz)
        db.commit()

        evaluate_automations_for_event(db, TRIGGER_LEAD_CREATED, biz.id)

        with patch("services.email_automation_service.get_email_provider", return_value=mock_provider):
            result = process_due_executions(db, limit=50)

        assert result["processed"] == 1
        assert result["retried"] == 1

        exec_record = db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.automation_id == auto.id,
        ).first()
        assert exec_record.status == EXEC_STATUS_SCHEDULED
        assert exec_record.retry_count == 1
        sched = exec_record.scheduled_at.replace(tzinfo=timezone.utc) if exec_record.scheduled_at.tzinfo is None else exec_record.scheduled_at
        assert sched > datetime.now(timezone.utc) - timedelta(seconds=5)

    def test_batch_execution_permanent_failure(self, db):
        mock_provider = MagicMock(spec=BaseEmailProvider)
        mock_provider.name = "mock"
        mock_provider.send_email.return_value = EmailSendResult(
            success=False,
            error="Invalid recipient domain (HTTP 400)",
            is_transient=False,
        )

        auto = create_automation(
            db=db,
            name="Permanent Fail Test",
            trigger_type=TRIGGER_LEAD_CREATED,
            subject_template="Test",
            body_template="Test",
            enabled=True,
            delay_minutes=0,
        )
        biz = Business(name="Permanent Biz", email="bad@invalid-domain")
        db.add(biz)
        db.commit()

        evaluate_automations_for_event(db, TRIGGER_LEAD_CREATED, biz.id)

        with patch("services.email_automation_service.get_email_provider", return_value=mock_provider):
            result = process_due_executions(db, limit=50)

        assert result["processed"] == 1
        assert result["failed"] == 1

        exec_record = db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.automation_id == auto.id,
        ).first()
        assert exec_record.status == EXEC_STATUS_FAILED
        assert "Invalid recipient" in exec_record.error_message

        # Verify email_failed activity recorded
        act = db.query(BusinessActivity).filter(
            BusinessActivity.business_id == biz.id,
            BusinessActivity.activity_type == "email_failed",
        ).first()
        assert act is not None
        assert "Email failed" in act.title

    def test_template_rendering_xss_and_unicode_safety(self, db):
        auto = create_automation(
            db=db,
            name="Security Test",
            trigger_type=TRIGGER_LEAD_CREATED,
            subject_template="Welcome <script>alert(1)</script> {{business_name}} 🚀",
            body_template="Hello <b>{{business_name}}</b> & welcome to \u00e9tude!",
            enabled=True,
        )
        biz = Business(
            name="Cafe & Bistro <img src=x onerror=1>",
            email="security@example.com",
        )
        db.add(biz)
        db.commit()

        execs = evaluate_automations_for_event(db, TRIGGER_LEAD_CREATED, biz.id)
        assert len(execs) == 1
        exec_record = execs[0]

        # Ensure unicode preserved and HTML is escaped
        assert "&lt;img src=x onerror=1&gt;" in exec_record.body_rendered or "Cafe &amp; Bistro" in exec_record.body_rendered
        assert "\u00e9tude" in exec_record.body_rendered
        assert "🚀" in exec_record.subject


class TestResendProviderSecurityAndReliability:
    """Security, error classification, and secret redaction tests for ResendEmailProvider."""

    def test_missing_api_key_fails_safely(self):
        provider = ResendEmailProvider(api_key="")
        result = provider.send_email(
            to_email="test@example.com",
            subject="Test",
            html_content="<p>Test</p>",
        )
        assert result.success is False
        assert result.is_transient is False
        assert "not configured" in result.error

    def test_recipient_email_validation_and_crlf_rejection(self):
        provider = ResendEmailProvider(api_key="re_valid_key_1234567890")
        
        # Test CRLF header injection attempt
        crlf_email = "victim@example.com\r\nBcc: attacker@evil.com"
        result = provider.send_email(to_email=crlf_email, subject="Test", html_content="Hello")
        assert result.success is False
        assert "Invalid recipient" in result.error

        # Test comma / multi recipient injection attempt
        multi_email = "victim@example.com, attacker@evil.com"
        result = provider.send_email(to_email=multi_email, subject="Test", html_content="Hello")
        assert result.success is False
        assert "Invalid recipient" in result.error

        # Test malformed email without @
        invalid_email = "plainaddress"
        result = provider.send_email(to_email=invalid_email, subject="Test", html_content="Hello")
        assert result.success is False

    def test_subject_crlf_sanitization(self):
        provider = ResendEmailProvider(api_key="re_mock_key_1234567890")
        
        with patch("providers.email_provider.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id": "res_123"})
            
            result = provider.send_email(
                to_email="user@example.com",
                subject="Hello\r\nInjected-Header: evil\nWorld",
                html_content="<p>Hello</p>",
            )
            assert result.success is True
            assert result.message_id == "res_123"
            
            # Check payload subject had CRLF stripped
            sent_payload = mock_post.call_args[1]["json"]
            assert "\r" not in sent_payload["subject"]
            assert "\n" not in sent_payload["subject"]
            assert "Hello Injected-Header: evil World" == sent_payload["subject"]

    def test_resend_error_classification_and_secret_redaction(self):
        secret_key = "re_live_secret_token_abcdef123456"
        provider = ResendEmailProvider(api_key=secret_key)

        # 429 Rate limit -> Transient
        with patch("providers.email_provider.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=429,
                text=f"Rate limit exceeded with key {secret_key}",
                json=lambda: {"message": f"Too many requests for key {secret_key}"},
            )
            result = provider.send_email("test@example.com", "Test", "Hello")
            assert result.success is False
            assert result.is_transient is True
            # Assert secret key is NOT leaked in error
            assert secret_key not in result.error
            assert "[REDACTED_API_KEY]" in result.error

        # 401 Unauthorized -> Permanent
        with patch("providers.email_provider.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=401,
                text="Invalid API token",
                json=lambda: {"message": "Invalid API token"},
            )
            result = provider.send_email("test@example.com", "Test", "Hello")
            assert result.success is False
            assert result.is_transient is False

        # Timeout -> Transient
        with patch("providers.email_provider.requests.post", side_effect=Exception(f"Connection timeout to {secret_key}")):
            result = provider.send_email("test@example.com", "Test", "Hello")
            assert result.success is False
            assert result.is_transient is True
            assert secret_key not in result.error


class TestAdvancedConcurrencyAndCascade:
    """Tests for concurrency, stale job recovery, and cascade deletion."""

    def test_stale_processing_execution_recovery(self, db):
        auto = create_automation(
            db=db,
            name="Stale Recovery Test",
            trigger_type=TRIGGER_LEAD_CREATED,
            subject_template="Test",
            body_template="Test",
        )
        biz = Business(name="Stale Biz", email="stale@example.com")
        db.add(biz)
        db.commit()

        # Create an execution stuck in 'processing' from 15 minutes ago
        old_time = datetime.now(timezone.utc) - timedelta(minutes=15)
        stale_exec = EmailAutomationExecution(
            automation_id=auto.id,
            business_id=biz.id,
            trigger_event=TRIGGER_LEAD_CREATED,
            trigger_key="stale_exec_key_1",
            status="processing",
            recipient_email="stale@example.com",
            subject="Stale Subject",
            body_rendered="Stale Body",
            scheduled_at=old_time,
            attempted_at=old_time,
            created_at=old_time,
            updated_at=old_time,
        )
        db.add(stale_exec)
        db.commit()

        # Running process_due_executions should recover it and process it
        result = process_due_executions(db, limit=10)
        assert result["processed"] == 1
        assert result["sent"] == 1

        db.refresh(stale_exec)
        assert stale_exec.status == EXEC_STATUS_SENT
        assert stale_exec.sent_at is not None

    def test_cascade_deletion_business_and_automation(self, db):
        auto = create_automation(
            db=db,
            name="Cascade Test",
            trigger_type=TRIGGER_LEAD_CREATED,
            subject_template="Subject",
            body_template="Body",
        )
        biz = Business(name="Cascade Biz", email="cascade@example.com")
        db.add(biz)
        db.commit()

        evaluate_automations_for_event(db, TRIGGER_LEAD_CREATED, biz.id)

        assert db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.business_id == biz.id,
        ).count() == 1

        # Delete business -> cascades and deletes execution
        db.delete(biz)
        db.commit()

        assert db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.business_id == biz.id,
        ).count() == 0

        # Create another business and execution, then delete automation
        biz2 = Business(name="Cascade Biz 2", email="cascade2@example.com")
        db.add(biz2)
        db.commit()

        evaluate_automations_for_event(db, TRIGGER_LEAD_CREATED, biz2.id)
        assert db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.automation_id == auto.id,
        ).count() == 1

        # Delete automation -> cascades and deletes execution
        db.delete(auto)
        db.commit()

        assert db.query(EmailAutomationExecution).filter(
            EmailAutomationExecution.automation_id == auto.id,
        ).count() == 0


class TestTemplateEngineSecurityDeep:
    """Security tests verifying code injection resistance, unicode, and large inputs."""

    def test_jinja_and_python_injection_resistance(self):
        from services.template_engine import render_template

        ctx = {
            "business_name": "Acme Corp",
            "contact_name": "Alice",
            "email": "alice@acme.example",
        }

        # Code injection payloads in templates
        template_payloads = [
            "{{ __import__('os').system('echo pwned') }}",
            "{{ 7 * 7 }}",
            "{{ config.items() }}",
            "{{ self.__class__.__mro__ }}",
            "{{ unknown_variable_should_stay }}",
        ]

        for p in template_payloads:
            rendered = render_template(p, ctx, escape_html=True)
            # Ensure no code was executed and unallowlisted placeholder remained literal string
            assert rendered == p
            assert "49" not in rendered

        # Injection payloads in context values
        xss_ctx = {
            "business_name": "<script>alert(document.cookie)</script>",
            "contact_name": '\"><img src=x onerror=alert(1)>',
            "email": "alice@acme.example",
        }
        xss_rendered = render_template("Hello {{contact_name}} from {{business_name}}", xss_ctx, escape_html=True)
        assert "<script>" not in xss_rendered
        assert "&lt;script&gt;" in xss_rendered
        assert "<img" not in xss_rendered
        assert "&lt;img" in xss_rendered

    def test_unicode_rtl_and_emoji_support(self):
        from services.template_engine import render_template

        ctx = {
            "business_name": "مرحبا بك 🌟 (Welcome)",
            "contact_name": "张伟",
            "email": "zhang@example.cn",
        }
        template = "Hello {{contact_name}}, welcome to {{business_name}}! 🎉"
        rendered = render_template(template, ctx)
        assert "张伟" in rendered
        assert "مرحبا بك 🌟 (Welcome)" in rendered
        assert "🎉" in rendered
