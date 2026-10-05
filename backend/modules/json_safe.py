"""JSON-safe conversion of a pandas frame into response records.

Pandas represents a SQL NULL as NaN wherever a column is not plain object
dtype, and pandas 3 does this for text columns too, where pandas 2 kept None.
NaN is not valid JSON; Starlette rejects it rather than emit the invalid `NaN`
literal, so a single NULL turns a whole endpoint into a 500 and the page that
reads it renders empty.

The tracker hit exactly that: `follow_up_date` and `hr_email_sent_at` are NULL
on most applications, which stayed None under the pandas 2 pinned here but
became NaN under the pandas 3 that an unpinned `pandas>=2.0.0` resolves to in
the deployed function. Every frame that becomes an HTTP response goes through
`json_records`, so a NULL stays a JSON null under either version.
"""


def json_records(frame):
    """Return `frame` as records with every missing value as None."""
    if frame is None:
        return []
    if not hasattr(frame, "to_dict"):
        return list(frame or [])
    if getattr(frame, "empty", False):
        return []
    return frame.astype(object).where(frame.notna(), None).to_dict("records")
