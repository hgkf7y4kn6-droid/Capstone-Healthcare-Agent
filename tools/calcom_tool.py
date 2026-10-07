"""
Cal.com integration: doctor availability lookup + appointment booking.

Docs: https://cal.com/docs/api-reference/v2/introduction

Auth: Bearer token from CALCOM_API_KEY (read from environment via config.py —
never hardcoded).

NOTE ON MODELING "DOCTORS": Cal.com's core objects are Event Types (the kind
of meeting, e.g. "Nephrology Consultation - Dr. Rao") owned by a user/team.
For this assistant, each "doctor" maps to a Cal.com Event Type. Set up event
types in your Cal.com account named like "<Specialty> - <Doctor Name>" (e.g.
"Nephrology - Dr. Rao") so the agent can match patient intent to the right
event type automatically.
"""

import requests
from typing import Optional
from crewai.tools import tool

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CALCOM_API_KEY, CALCOM_BASE_URL
from data.patient_db import record_appointment, find_patient
from monitoring.monitor import track


def _headers():
    if not CALCOM_API_KEY:
        raise RuntimeError(
            "CALCOM_API_KEY is not set. Add it to your .env file (see .env.example). "
            "Never hardcode API keys in source files."
        )
    return {
        "Authorization": f"Bearer {CALCOM_API_KEY}",
        "Content-Type": "application/json",
        "cal-api-version": "2024-08-13",
    }


@tool("list_event_types")
def list_event_types(specialty_keyword: Optional[str] = None) -> str:
    """List available doctor event types on Cal.com, optionally filtered by a
    specialty keyword (e.g. 'nephrology', 'cardiology'). Use this first to
    discover which event type ID matches the patient's requested specialty."""
    with track("calcom.list_event_types") as status:
        try:
            resp = requests.get(f"{CALCOM_BASE_URL}/event-types", headers=_headers(), timeout=15)
            resp.raise_for_status()
            data = resp.json()
            event_types = data.get("data", data) if isinstance(data, dict) else data
            results = []
            for et in event_types if isinstance(event_types, list) else []:
                title = et.get("title", "")
                if specialty_keyword and specialty_keyword.lower() not in title.lower():
                    continue
                results.append({
                    "id": et.get("id"),
                    "title": title,
                    "slug": et.get("slug"),
                    "length_minutes": et.get("length"),
                })
            if not results:
                status["success"] = False
                return f"No event types found matching '{specialty_keyword}'. Ask the attendant to verify the specialty name or check the Cal.com account setup."
            return str(results)
        except requests.exceptions.RequestException as e:
            status["success"] = False
            return f"Cal.com API error while listing event types: {e}"


@tool("get_available_slots")
def get_available_slots(event_type_id: int, start_date: str, end_date: str, timezone: str = "UTC") -> str:
    """Get available booking slots for a given Cal.com event_type_id between
    start_date and end_date (format YYYY-MM-DD). Use list_event_types first
    to find the correct event_type_id for the requested doctor/specialty."""
    with track("calcom.get_available_slots") as status:
        try:
            params = {
                "eventTypeId": event_type_id,
                "start": start_date,
                "end": end_date,
                "timeZone": timezone,
            }
            resp = requests.get(f"{CALCOM_BASE_URL}/slots", headers=_headers(), params=params, timeout=15)
            resp.raise_for_status()
            return str(resp.json())
        except requests.exceptions.RequestException as e:
            status["success"] = False
            return f"Cal.com API error while fetching slots: {e}"


@tool("book_appointment")
def book_appointment(event_type_id: int, start_time_iso: str, attendee_name: str,
                      attendee_email: str, patient_full_name: str, specialty: str = "",
                      doctor_name: str = "", timezone: str = "UTC", notes: str = "") -> str:
    """Book a confirmed appointment on Cal.com for a given event_type_id at a
    specific start_time_iso (ISO 8601, e.g. '2025-09-10T09:00:00Z'). Requires
    attendee_name and attendee_email (the person managing the booking — may
    be different from the patient, e.g. an adult child booking for a parent).
    patient_full_name links the booking back to the local patient record.
    After a successful booking, this also records the appointment in the
    local patient database so it appears in history and the dashboard."""
    with track("calcom.book_appointment") as status:
        try:
            payload = {
                "eventTypeId": event_type_id,
                "start": start_time_iso,
                "attendee": {
                    "name": attendee_name,
                    "email": attendee_email,
                    "timeZone": timezone,
                },
                "metadata": {"notes": notes, "patient": patient_full_name, "specialty": specialty},
            }
            resp = requests.post(f"{CALCOM_BASE_URL}/bookings", headers=_headers(), json=payload, timeout=15)
            resp.raise_for_status()
            booking = resp.json()
            booking_data = booking.get("data", booking) if isinstance(booking, dict) else booking
            booking_uid = booking_data.get("uid") or booking_data.get("id")
            end_time = booking_data.get("end", "")

            patient = find_patient(patient_full_name)
            if patient:
                record_appointment(
                    patient_id=patient["patient_id"],
                    doctor_name=doctor_name or "Unknown",
                    specialty=specialty,
                    calcom_booking_uid=str(booking_uid),
                    start_time=start_time_iso,
                    end_time=end_time,
                )
            return f"Appointment booked successfully. Booking reference: {booking_uid}. Details: {booking_data}"
        except requests.exceptions.RequestException as e:
            status["success"] = False
            return f"Cal.com API error while booking: {e}. The appointment was NOT booked — do not tell the patient it succeeded."


CALCOM_TOOLS = [list_event_types, get_available_slots, book_appointment]
