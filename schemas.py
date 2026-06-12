

# ---------------------------------------------------------------------------
# generate_executive_report output
# ---------------------------------------------------------------------------

class ExecutiveReportOutput(BaseModel):
    ticker:               str
    form_type:            str
    pipeline_success:     bool
    failure_reason:       Optional[str]
    report:               Optional[str]   # the full formatted report string
    filing_date:          Optional[str]
    overall_materiality:  Optional[str]   # "low" | "moderate" | "high" | "critical"
    top_signals:          list[str]
    elapsed_seconds:      float
