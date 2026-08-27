from apex_memory import MemoryStore, ModelMetric, build_model_comparison, trim_context

def test_memory_is_user_scoped_and_searchable(tmp_path):
    store=MemoryStore(tmp_path/'m.db'); store.remember('a','dose question','answer'); store.remember('b','other','private')
    assert len(store.recent('a'))==1 and store.search('a','dose')[0]['answer']=='answer'
    assert not store.search('a','private')

def test_preferences_metrics_and_context(tmp_path):
    store=MemoryStore(tmp_path/'m.db'); store.set_preferences('a',{'theme':'dark'}); assert store.preferences('a')['theme']=='dark'
    store.record_metric(ModelMetric('local',12,30,True)); assert store.analytics()[0]['success_rate']==1
    assert '### local' in build_model_comparison({'local':'yes'})
    assert len(trim_context([{'question':'x','answer':'y'}],20)) <= 20
