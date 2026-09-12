"""Observer-only source colors derived from completed responses and real tasks."""
SOURCE_COLORS = {
    'unseen': '#8996a5',
    'detected': '#c17a2b',
    'target': '#8961c0',
    'cleared': '#29816a',
}
SOURCE_LABELS = {
    'unseen': '未扫描到',
    'detected': '已扫描到',
    'target': '当前目标',
    'cleared': '已清除',
}


def selected_source_channel(state):
    """Exclude routine channel scans and unselected candidate proposals."""
    if state.get('status') == 'mission ended':
        return None
    strategy = state.get('strategy', {})
    if strategy.get('phase') == 'finished':
        return None
    active_clear = state.get('active_clear') or {}
    target = strategy.get('current_target') or {}
    channel = None
    if active_clear:
        channel = active_clear.get('channel')
    elif (strategy.get('phase') == 'service' or target.get('kind') == 'clear'
            or target.get('role') in ('selected_service', 'target_followup', 'mec', 'near')):
        channel = target.get('channel')
    if channel is None and not target and strategy.get('phase') in ('service', 'tail'):
        tasks = strategy.get('tasks', [])
        if tasks and tasks[0].get('kind') == 'service':
            channel = tasks[0].get('channel')
    return None if channel in state.get('cleared', ()) else channel


def source_status(state, channel):
    if channel in state.get('cleared', ()):
        return 'cleared'
    if channel == selected_source_channel(state):
        return 'target'
    if channel in state.get('detected', ()):
        return 'detected'
    return 'unseen'
