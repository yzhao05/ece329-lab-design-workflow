"""Conversation questions are read-only in guided and EMVR modes."""
from copy import deepcopy

import pytest

from ece329_workflow.design_versions import ensure_initial_version, record_design_version
from ece329_workflow.dialogue_acts import apply_stage_field_updates
from ece329_workflow.dialogue_state import current_pending_action, resolved_intent, UserIntent
from ece329_workflow.engine import WorkflowEngine
from ece329_workflow.generator import RuleBasedStageGenerator
from ece329_workflow.meta_dialogue import meta_question_kinds
from ece329_workflow.models import DesignSession, InteractionState, Stage, WorkflowStatus


class UnavailableGenerator:
    def generate(self, *args, **kwargs):
        raise AssertionError('Meta questions must not need the model')

    def resolve_intent(self, *args, **kwargs):
        raise AssertionError('Meta questions must not become a design answer')


@pytest.fixture(params=list(InteractionState))
def mode(request):
    return request.param


def engine_session(mode, stage=Stage.CONCEPTUAL_PROCEDURE):
    session = DesignSession(design_id='meta-question', interaction_state=mode,
                            current_stage_index=list(Stage).index(stage))
    pending = {'type': 'ANSWER_EMVR_STAGE_QUESTION' if mode is InteractionState.EMVR_DIRECT else 'ANSWER_STAGE_QUESTION',
               'stage': stage.value, 'subject': 'procedure_steps', 'answer_fields': ['procedure_steps'],
               'question': '请明确加载基准后如何保存并比较实验快照。',
               'candidate_answer': '待审阅草稿', 'candidate_binding_authorized': True,
               'repeat_count': 2, 'action_id': 'original-question'}
    session.model_context['dialogue_state'] = {'pending_action': pending}
    session.stage_outputs[stage.value] = {'stage_payload': {'procedure_steps': ['原步骤'],
            'pending_action': deepcopy(pending)}, 'student_task': pending['question']}
    engine = WorkflowEngine(generator=UnavailableGenerator())
    engine.store.save(session)
    return engine, session


@pytest.mark.parametrize('message', [
    '你现在在问什么问题，明确告诉我', '我有点乱，你现在需要我回答什么？',
    '先别继续，我们现在到哪一步了？', '为什么你又问这个问题？',
    '我该怎么回答？', 'What are you asking me right now?',
])
def test_meta_questions_preserve_design_stage_and_pending_even_with_stale_buttons(mode, message):
    e,s = engine_session(mode)
    before = deepcopy(s)
    result = e.process_turn(s.design_id, {'message': message, 'complete_stage': True,
                                       'selected_option_id': 'old-continue'})
    stored = e.store.get(s.design_id)
    assert before.design_context == stored.design_context
    assert before.model_context == stored.model_context
    assert before.stage_outputs == stored.stage_outputs
    assert before.current_stage == stored.current_stage
    assert before.completed_stages == stored.completed_stages
    assert result['stage_payload']['meta_question']
    assert before.model_context['dialogue_state']['pending_action']['question'] in result['assistant_message']
    assert '第3阶段' in result['assistant_message']
    assert result['student_task'] is None
    assert result['transitioned_from_stage'] is None
    assert len(stored.history) == len(before.history) + 1


def test_mode_question_does_not_switch_guided_mode_when_emvr_is_mentioned(mode):
    e,s = engine_session(mode)
    result = e.process_turn(s.design_id, {'message': '引导模式和EMVR模式有什么区别？我们现在是什么模式？'})
    assert e.store.get(s.design_id).interaction_state is mode
    assert '两种模式都先回应你的问题' in result['assistant_message']


def test_first_public_stage_meta_question_is_not_rejected_as_off_topic(mode):
    e,s = engine_session(mode, Stage.IDEA_BRAINSTORMING)
    result = e.process_turn(s.design_id, {'message': '你现在在问什么问题，明确告诉我'})
    assert result['request_rejected'] is False
    assert '第1阶段' in result['assistant_message']
    assert '课外' not in result['assistant_message']
    assert '正在整理“实验流程”' in result['assistant_message']


def test_repeated_meta_questions_do_not_reset_pending_or_approve_candidate(mode):
    e,s = engine_session(mode)
    pending = current_pending_action(s)
    for message in ['你现在在问什么问题？', '我还是有点乱，你要我确认什么？', '现在到哪一步了？']:
        e.process_turn(s.design_id, {'message': message})
        assert current_pending_action(e.store.get(s.design_id)) == pending


def test_unknown_question_and_history_are_not_invented(mode):
    e,s = engine_session(mode)
    s.model_context = {}
    s.stage_outputs = {}
    e.store.save(s)
    result = e.process_turn(s.design_id, {'message': '你现在在问什么问题？刚才修改了哪些内容？'})
    assert '没有保存可核对的具体待答问题' in result['assistant_message']
    assert '没有可核对的修改前后版本' in result['assistant_message']


def test_recent_changes_use_saved_versions_and_meta_turns_do_not_create_versions(mode):
    e,s = engine_session(mode)
    ensure_initial_version(s)
    apply_stage_field_updates(s, [{'field':'procedure_steps','operation':'REPLACE','value':['加载基准','保存快照']}],stage=s.current_stage)
    record_design_version(s, changed_fields=['procedure_steps'], reason='修改流程', source='TEST')
    versions = deepcopy(s.model_context['design_versions'])
    e.store.save(s)
    for _ in range(2):
        result = e.process_turn(s.design_id, {'message': '刚才修改了哪些内容？'})
        assert '实验流程' in result['assistant_message'] and '保存快照' in result['assistant_message']
        assert e.store.get(s.design_id).model_context['design_versions'] == versions


def test_completed_design_status_question_does_not_reopen_design(mode):
    e,s = engine_session(mode, Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT)
    s.status = WorkflowStatus.COMPLETE
    e.store.save(s)
    result = e.process_turn(s.design_id, {'message': '现在到哪一步了？'})
    assert result['workflow_status'] == 'complete'
    assert '没有等待你回答的新问题' in result['assistant_message']
    assert e.store.get(s.design_id).status is WorkflowStatus.COMPLETE


def test_meta_turn_is_idempotent(mode):
    e,s = engine_session(mode)
    request = {'message': '你现在在问什么问题？', 'turn_id': 'meta-turn-1'}
    first = e.process_turn(s.design_id, request)
    second = e.process_turn(s.design_id, request)
    assert second == first
    assert len(e.store.get(s.design_id).history) == 1


def test_legacy_question_recovers_for_display_without_changing_live_pending(mode):
    e,s = engine_session(mode)
    s.history = [{'handled_stage':s.current_stage.value,'output':deepcopy(s.stage_outputs[s.current_stage.value])}]
    s.model_context['dialogue_state']['pending_action'] = None
    e.store.save(s)
    for _ in range(2):
        result = e.process_turn(s.design_id, {'message': '你现在在问什么问题？'})
        assert '加载基准' in result['assistant_message']
        assert current_pending_action(e.store.get(s.design_id)) is None


@pytest.mark.parametrize('message', [
    '为什么磁场方向会改变？', '实验流程：加载基准。改变电流。保存快照。',
    '把报告问题改为“你现在在问什么问题”',
    '现在到哪一步了？把电流改为2A。', '切换到EMVR模式',
    '现在到哪一步了？电流默认2A。',
])
def test_physics_questions_quoted_text_and_real_edits_keep_their_own_route(message):
    assert not meta_question_kinds(message)


def test_course_answer_still_updates_the_pending_field_after_meta_help(mode):
    e,s = engine_session(mode)
    e.process_turn(s.design_id, {'message': '你现在在问什么问题？'})
    # The real engine path must remain usable after a read-only turn.
    class AnswerGenerator(RuleBasedStageGenerator):
        def resolve_intent(self, session, user_message, pending_action, carried_context):
            assert pending_action['action_id'] == 'original-question'
            return resolved_intent(UserIntent.ANSWER_CURRENT_QUESTION, confidence=1.0,
                source='TEST', actions_authoritative=True,
                dialogue_acts=[{'type':'MODIFY_STAGE_FIELD','target':'procedure_steps',
                               'operation':'REPLACE','content':user_message,'confidence':1.0}])
    e.generator = AnswerGenerator()
    result = e.process_turn(s.design_id, {'message': '实验流程：加载默认基准。改变电流方向。等待场线刷新。保存结果快照。并排比较方向。解释观察差异。'})
    assert not result['stage_payload'].get('meta_question')
    assert '保存结果快照' in str(e.store.get(s.design_id).design_context)


def test_meta_question_preserves_recorded_export_failure():
    e,s = engine_session(InteractionState.EMVR_DIRECT, Stage.STUDENT_SYNTHESIS_OR_EMVR_OUTPUT)
    s.model_context['artifact_blocker'] = {'error':'EMVR报告仍缺少：完整实验流程','fingerprint':'old','message':'旧提示'}
    before = deepcopy(s.model_context)
    e.store.save(s)
    result = e.process_turn(s.design_id, {'message':'为什么卡在这里？你现在在问什么问题？'})
    assert '完整实验流程' in result['assistant_message']
    assert e.store.get(s.design_id).model_context == before


def test_default_approval_still_completes_after_meta_question():
    from tools.export_emvr34_regression import completed_magnetic_engine
    from tests.test_emvr35_recovery import prepare
    e,s = prepare(completed_magnetic_engine()[1], broken=True, final=True)
    e.process_turn(s.design_id, {'message':'恢复之前的实验流程'})
    pending = current_pending_action(e.store.get(s.design_id))
    assert pending['default_proposal_value']
    result = e.process_turn(s.design_id, {'message':'你现在需要我确认什么？','complete_stage':True})
    assert current_pending_action(e.store.get(s.design_id)) == pending
    assert result['stage_payload']['pending_action']['default_proposal_value'] == pending['default_proposal_value']
    result = e.process_turn(s.design_id, {'message':'批准默认方案','complete_stage':True})
    assert result['workflow_status'] == 'complete' and result['builder_input_ready']
