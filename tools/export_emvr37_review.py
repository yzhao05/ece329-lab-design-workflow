"""Reproduce a synthetic handoff through reference adoption and real PDF export."""
import json
from math import ceil, cos, pi, sin
from pathlib import Path

from ece329_workflow.builder_defaults import build_implementation_defaults, format_implementation_defaults, record_implementation_defaults_approval
from ece329_workflow.builder_input import build_builder_gate1_input
from ece329_workflow.builder_requirements import builder_requirement_values
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.models import Stage, WorkflowStatus
from tools.export_emvr35_review import MU, RADIUS, completed_ring_engine


def reviewed_engine():
    engine, session = completed_ring_engine()
    session.status = WorkflowStatus.ACTIVE
    session.current_stage_index = list(Stage).index(Stage.EXPECTED_DATA_VISUALIZATION)
    field = 'numerical_model_specifications'
    constants = builder_requirement_values(session)['model_constants_and_media']
    constants += '；有向源路径r(u)=(0.5*cos(u),0.5*sin(u),0) m，u=0..2*pi，正I沿u递增。'
    apply_stage_field_updates(session, [
        {'field':'model_constants_and_media','operation':'REPLACE','value':constants},
        {'field':field,'operation':'REPLACE','value':'毕奥-萨伐尔数值积分，RK4；计算域3m，误差1e-5，边界外终止，最大5000步。'},
    ], stage=session.current_stage)
    session.model_context['dialogue_state'] = {'pending_action':{
        'type':'ANSWER_EMVR_STAGE_QUESTION','interaction_state':'EMVR_DIRECT',
        'subject':field,'answer_fields':[field],'question':'请补充数值离散与种子定义。'}}
    engine.store.save(session)
    response = engine.process_turn(session.design_id, {'message':'给我个完整的参考'})
    assert response['stage_payload']['reference_draft']['field'] == field
    engine.process_turn(session.design_id, {'message':'采用'})
    session = engine.store.get(session.design_id)
    assert '源路径段长设为0.01 m' in builder_requirement_values(session)[field]
    assert '距源不大于0.03 m' in builder_requirement_values(session)[field]
    session.current_stage_index = list(Stage).index(Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT)
    contract = format_implementation_defaults(build_implementation_defaults(session))
    apply_stage_field_updates(session,[{'field':'implementation_defaults','operation':'REPLACE','value':contract}],stage=session.current_stage)
    record_implementation_defaults_approval(session, source='SYNTHETIC_REVIEW_NOT_USER_APPROVAL')
    engine.store.save(session)
    result = engine.process_turn(session.design_id, {'message':'继续','complete_stage':True})
    assert result['workflow_status'] == 'complete', result.get('completion_error')
    return engine, engine.store.get(session.design_id)


def axial_checks():
    """Independent finite source calculation for the adopted 0.01/0.005 m grid."""
    results = []
    for step in (0.01,0.005):
        count = ceil(2*pi*RADIUS/step)
        for z in (0.0,0.5,1.0):
            actual = 0.0
            for k in range(count):
                a,b = 2*pi*k/count, 2*pi*(k+1)/count
                p,q = (RADIUS*cos(a),RADIUS*sin(a)), (RADIUS*cos(b),RADIUS*sin(b))
                dx,dy = q[0]-p[0],q[1]-p[1]
                rx,ry = -(p[0]+q[0])/2,-(p[1]+q[1])/2
                actual += MU/(4*pi)*(dx*ry-dy*rx)/(rx*rx+ry*ry+z*z)**1.5
            expected = MU*RADIUS**2/(2*(RADIUS**2+z*z)**1.5)
            assert abs(actual-expected) < 1e-9
            results.append({'source_step_m':step,'segments':count,'z_m':z,'Bz_T':actual,
                            'analytic_Bz_T':expected,'absolute_error_T':abs(actual-expected)})
    return results


def main():
    checks = axial_checks()
    engine, session = reviewed_engine()
    output = Path('output/pdf/emvr37-builder-review.pdf')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_bytes(engine.render_builder_input_pdf(session.design_id))
    scratch = Path('build/emvr37-review')
    scratch.mkdir(parents=True,exist_ok=True)
    (scratch/'builder-input.json').write_text(json.dumps(build_builder_gate1_input(session),ensure_ascii=False,indent=2),encoding='utf-8')
    (scratch/'numeric-checks.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    print(f'{output}: reference adopted, PDF exported; {len(checks)} axial checks passed. Synthetic only; no Unity execution.')


if __name__ == '__main__':
    main()
