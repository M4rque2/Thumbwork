import copy
import unittest
from thumbwork.compaction import ContextManager
from thumbwork.llm_client import LLMResult

class SummaryModel:
    def __init__(self):self.calls=[];self.fail=False
    def invoke(self,messages,**kwargs):
        self.calls.append((messages,kwargs))
        if self.fail: raise RuntimeError('summary failed')
        return LLMResult('Collected A; back failed twice; retry via top-left control; B is pending.')

class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.model=SummaryModel();self.manager=ContextManager(self.model,6000)
        self.state={'history':[{'output':f'Turn {i}: collected A; failed Back; pending B. '+'x'*1000} for i in range(6)],'summary':'','previous_expectation':'B visible'}
    @staticmethod
    def build(state):
        return [{'role':'system','content':[{'text':'Collect A and B'}]},
            {'role':'user','content':[{'text':state['summary']}, *[{'text':e['output']} for e in state['history']], {'text':'Current page B; expectation B visible'}]}]
    def test_compaction_keeps_memory_and_recent_turns(self):
        result=self.manager.prepare(self.state,self.build)
        self.assertEqual(len(self.state['history']),2)
        self.assertIn('pending',self.state['summary'])
        self.assertEqual(self.state['previous_expectation'],'B visible')
        self.assertLess(self.manager.counter.count(result),4200)
        self.assertGreater(len(self.model.calls),1) # Old transcript is chunked.
        for messages,kwargs in self.model.calls:
            self.assertLess(self.manager.counter.count(messages),self.manager.input_limit)
            self.assertIn('max_tokens',kwargs)
            self.assertFalse(any('image' in p for m in messages for p in m['content']))
    def test_summary_failure_is_transactional(self):
        before=copy.deepcopy(self.state);self.model.fail=True
        with self.assertRaisesRegex(RuntimeError,'summary failed'):self.manager.prepare(self.state,self.build)
        self.assertEqual(self.state,before)
    def test_repeated_compaction_and_forced_retry(self):
        self.manager.prepare(self.state,self.build)
        self.state['history'].extend([{'output':'next action '+'z'*1400} for _ in range(3)])
        self.manager.prepare(self.state,self.build)
        self.assertEqual(self.state['compactions'],2)
        self.manager.prepare(self.state,self.build,force=True)
        self.assertEqual(self.state['compactions'],3)
    def test_indispensable_oversized_input_fails(self):
        with self.assertRaisesRegex(RuntimeError,'exceed'):
            self.manager.prepare({'history':[],'summary':''},lambda s:[{'role':'user','content':[{'text':'x'*9000}]}])
