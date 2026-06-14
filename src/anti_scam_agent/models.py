from pydantic import BaseModel, Field
from typing import Annotated
from enum import Enum

class FakePersona(BaseModel):
    name: str
    email: str
    password: str
    phone: str
    address: str
    credit_card_number: str
    credit_card_number_luhn_valid: str
    credit_card_expiry: str
    credit_card_cvv: str

class Outcome(str, Enum):
    not_attempted = "not_attempted"
    failed = "failed"
    unclear = "unclear"
    succeeded = "succeeded"

class BrowsingResult(BaseModel):
    website_summary: Annotated[str, Field(description="A concise summary of the website's apparent purpose and content.")]
    outgoing_links: Annotated[list[str], Field(description="External hostnames the browser navigated to during the visit (different from the target domain).")]
    login_attempted: Annotated[bool, Field(description="Whether a login or registration flow was attempted.")]
    login_outcome: Annotated[Outcome, Field(default=Outcome.not_attempted, description="The result of the login or registration: 'succeeded' only if an explicit confirmation appeared, 'failed' if it was explicitly rejected, 'unclear' if there was no clear response, 'not_attempted' if it was never tried.")]
    credit_card_submitted: Annotated[bool, Field(description="Whether credit card information was submitted to the site.")]
    payment_outcome: Annotated[Outcome, Field(default=Outcome.not_attempted, description="The result of the payment: 'succeeded' only if an explicit confirmation appeared, 'failed' if it was explicitly rejected or declined, 'unclear' if there was no clear response, 'not_attempted' if no payment was made.")]
    form_fields_requested: Annotated[list[str], Field(description="Types of personal information the site requested (e.g. 'full name', 'ID number', 'credit card').")]
    unexpected_events: Annotated[list[str], Field(description="Anything that happened during the visit that an ordinary user would find surprising (e.g. 'redirected to an unrelated domain', 'payment confirmation page appeared instantly without a processor redirect').")]
    visit_completed: Annotated[bool, Field(default=True, description="Whether the visit ran to a normal conclusion rather than being cut short.")]

class ScamAssessment(BaseModel):
    is_scam: Annotated[bool, Field(description="True if the site is assessed to be a scam or phishing site.")]
    confidence: Annotated[float, Field(ge=0.0, le=1.0, description="Confidence score from 0.0 (not confident) to 1.0 (very confident).")]
    scam_type: Annotated[str | None, Field(description="Category of scam, e.g. 'phishing', 'fake lottery', 'credit card harvesting'. None if not a scam.")]
    reasoning: Annotated[str, Field(description="Detailed explanation of the assessment, citing specific evidence.")]
    risk_factors: Annotated[list[str], Field(description="Specific observations that contributed to the risk assessment.")]
