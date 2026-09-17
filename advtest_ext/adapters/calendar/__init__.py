"""Calendar adapter — :class:`CalendarTarget` is a ``DocumentTarget`` that
materialises an :class:`AbstractDocument` as a real Google Calendar
event, asks Gemini-for-Workspace to summarise the day / event, and
tears the event down afterwards.

See ``TECHNIQUE_COVERAGE.md`` for the per-technique support matrix;
the event ``description`` is the natural fit for body text and the
event ``extendedProperties`` become the metadata channel, but a
Calendar event has no analogue for comments / footnotes / image
rendering / suggested edits / etc.
"""

from advtest_ext.adapters.calendar.target import CalendarTarget

__all__ = ["CalendarTarget"]
