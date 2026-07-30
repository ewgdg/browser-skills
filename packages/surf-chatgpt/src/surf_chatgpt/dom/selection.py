from __future__ import annotations

import json


def picker_selection_source(
    *, model_query: str | None, thinking_query: str | None
) -> str:
    """Build helpers that select and affirm only requested picker dimensions."""
    return f"""
  const desiredModelQuery = {json.dumps(model_query)};
  const desiredThinkingQuery = {json.dumps(thinking_query)};

  function compactLabel(value) {{
    return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
  }}

  function isDisabled(node) {{
    const label = textOf(node).toLowerCase();
    return node.hasAttribute('disabled') || node.getAttribute('aria-disabled') === 'true' ||
      node.getAttribute('data-disabled') === 'true' ||
      /upgrade|unavailable|limit reached/.test(label);
  }}

  function isChecked(node) {{
    return node.getAttribute('aria-checked') === 'true' ||
      node.getAttribute('data-state') === 'checked';
  }}

  function settlePickerMutation() {{
    return new Promise((resolve) => requestAnimationFrame(() => resolve()));
  }}

  function findPickerButton() {{
    const explicit = firstVisible(['[data-testid="model-switcher-dropdown-button"]']);
    if (explicit) return explicit;
    return Array.from(
      document.querySelectorAll('button[aria-haspopup="menu"], [role="button"][aria-haspopup="menu"]')
    ).find((node) => {{
      if (!isVisible(node) || !node.closest('main, form')) return false;
      const evidence = [
        textOf(node),
        node.getAttribute('aria-label'),
        node.getAttribute('data-testid')
      ].join(' ');
      return /model|gpt|thinking|instant|medium|high|pro/i.test(evidence);
    }}) || null;
  }}

  function visibleMenuItems() {{
    return Array.from(document.querySelectorAll(
      '[role="menu"] [role="menuitemradio"], [role="menu"] [role="menuitem"], ' +
      '[data-radix-menu-content] [role="menuitemradio"], ' +
      '[data-radix-menu-content] [role="menuitem"], [cmdk-item]'
    )).filter(isVisible).map((node) => ({{
      node,
      label: textOf(node),
      disabled: isDisabled(node),
      hasSubmenu: node.getAttribute('aria-haspopup') === 'menu'
    }})).filter((item) => item.label);
  }}

  function bestLabelMatch(items, query) {{
    if (compactLabel(query) === 'latest') {{
      return items.find((item) => !item.disabled) || null;
    }}
    const availableItems = items.filter((item) => !item.disabled);
    const compactQuery = compactLabel(query);
    const exactMatch = availableItems.find(
      (item) => compactLabel(item.label) === compactQuery
    );
    if (exactMatch) return exactMatch;
    const substringMatch = availableItems.find(
      (item) => compactLabel(item.label).includes(compactQuery)
    );
    if (substringMatch) return substringMatch;
    // Accept fuzzy multi-word input only when every meaningful query token
    // appears in one label, avoiding arbitrary scores and weak matches.
    const queryTokens = String(query).toLowerCase().match(/[a-z]{{2,}}|\\d{{2,}}/g) || [];
    if (!queryTokens.length) return null;
    return availableItems.find((item) => {{
      const compactItem = compactLabel(item.label);
      return queryTokens.every((token) => compactItem.includes(token));
    }}) || null;
  }}

  async function openPicker() {{
    document.dispatchEvent(new KeyboardEvent('keydown', {{
      key: 'Escape', code: 'Escape', bubbles: true, cancelable: true
    }}));
    const button = findPickerButton();
    if (!button) return null;
    dispatchClickSequence(button);
    await settlePickerMutation();
    return visibleMenuItems().length ? visibleMenuItems() : null;
  }}

  async function selectRequestedModel() {{
    const topLevelItems = await openPicker();
    if (!topLevelItems) return {{state: 'ui_changed'}};
    const modelSubmenu = topLevelItems.find((item) =>
      item.hasSubmenu && /model|gpt|more/i.test(item.label)
    );
    if (!modelSubmenu) return {{state: 'ui_changed'}};
    const topLevelNodes = new Set(topLevelItems.map((item) => item.node));
    modelSubmenu.node.dispatchEvent(new MouseEvent('mouseover', {{
      bubbles: true, cancelable: true, view: window
    }}));
    dispatchClickSequence(modelSubmenu.node);
    await settlePickerMutation();
    const modelItems = visibleMenuItems().filter((item) => !topLevelNodes.has(item.node));
    const match = bestLabelMatch(modelItems, desiredModelQuery);
    if (!match) return {{state: 'model_unavailable'}};
    dispatchClickSequence(match.node);
    await settlePickerMutation();
    if (!isChecked(match.node)) return {{state: 'model_unavailable'}};
    return {{label: match.label}};
  }}

  async function selectRequestedThinking() {{
    const topLevelItems = await openPicker();
    if (!topLevelItems) return {{state: 'ui_changed'}};
    const thinkingItems = topLevelItems.filter((item) => !item.hasSubmenu);
    const match = bestLabelMatch(thinkingItems, desiredThinkingQuery);
    if (!match) return {{state: 'model_unavailable'}};
    dispatchClickSequence(match.node);
    await settlePickerMutation();
    if (!isChecked(match.node)) return {{state: 'model_unavailable'}};
    return {{label: match.label}};
  }}

  async function selectRequestedDimensions() {{
    const selection = {{}};
    if (desiredModelQuery !== null) {{
      const model = await selectRequestedModel();
      if (model.state) return model;
      selection.model = model.label;
    }}
    if (desiredThinkingQuery !== null) {{
      const thinking = await selectRequestedThinking();
      if (thinking.state) return thinking;
      selection.thinking = thinking.label;
    }}
    return {{selection}};
  }}
""".strip()
