from io import BytesIO
import pdfplumber

from ece329_workflow.reporting import render_design_report_pdf, english_pdf_text


def test_english_punctuation_preserves_equations_and_paths():
    assert english_pdf_text('Value：B=μ₀I/(2R)；r²。Path（Assets/Test.cs）') == 'Value:B=μ₀I/(2R);r².Path(Assets/Test.cs)'


def test_english_long_summary_starts_in_remaining_space_and_retains_ending():
    report = {'title': 'ECE329 Guided Experimental Design Summary', 'design_id': 'layout-test',
        'source_design': {'revision': 12, 'fingerprint': 'abc123'}, 'status': 'complete',
        'idea': 'Compare magnetic fields at fixed current values.', 'sections': [
            {'title': 'Experiment method', 'items': [{'label': 'Procedure', 'value': 'Set the current and observe the field.'}]},
            {'title': 'Student summary', 'items': [{'label': 'Summary', 'value':
                'START-LONG-SUMMARY B=μ₀I/(2R); r². ' + 'Compare the observed field with the theoretical prediction. ' * 200 + ' END-LONG-SUMMARY'}]},
        ]}
    with pdfplumber.open(BytesIO(render_design_report_pdf(report, language='en'))) as pdf:
        assert 'START-LONG-SUMMARY' in pdf.pages[0].extract_text()
        assert 'END-LONG-SUMMARY' in pdf.pages[-1].extract_text()
        assert len(pdf.pages) > 2
        for page in pdf.pages:
            assert 'Page ' in page.extract_text()
            assert all(0 <= c['x0'] <= c['x1'] <= page.width + .1 and
                       0 <= c['top'] < c['bottom'] <= page.height + .1 for c in page.chars)
            assert not any(c['fontname'].endswith('ZapfDingbats') for c in page.chars)
