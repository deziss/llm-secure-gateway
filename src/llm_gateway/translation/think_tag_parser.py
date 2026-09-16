from typing import Tuple

class ThinkTagParser:
    """Stateful streaming parser to strip and isolate <think>...</think> blocks from content streams."""
    
    def __init__(self) -> None:
        self.in_thinking = False
        self.buffer = ""
        
    def feed(self, text: str) -> Tuple[str, str]:
        """Feed text chunk. Returns (thinking_delta, standard_delta)."""
        if not text:
            return "", ""
            
        combined = self.buffer + text
        self.buffer = ""
        
        thinking_out = []
        standard_out = []
        
        i = 0
        n = len(combined)
        
        while i < n:
            if not self.in_thinking:
                open_idx = combined.find("<think>", i)
                if open_idx != -1:
                    standard_out.append(combined[i:open_idx])
                    self.in_thinking = True
                    i = open_idx + 7
                else:
                    remaining = combined[i:]
                    potential_starts = ["<", "<t", "<th", "<thi", "<thin", "<think"]
                    matched_partial = False
                    for p in reversed(potential_starts):
                        if remaining.endswith(p):
                            self.buffer = p
                            standard_out.append(remaining[:-len(p)])
                            matched_partial = True
                            break
                    if matched_partial:
                        break
                    else:
                        standard_out.append(remaining)
                        break
            else:
                close_idx = combined.find("</think>", i)
                if close_idx != -1:
                    thinking_out.append(combined[i:close_idx])
                    self.in_thinking = False
                    i = close_idx + 8
                else:
                    remaining = combined[i:]
                    potential_ends = ["<", "</", "</t", "</th", "</thi", "</thin", "</think"]
                    matched_partial = False
                    for p in reversed(potential_ends):
                        if remaining.endswith(p):
                            self.buffer = p
                            thinking_out.append(remaining[:-len(p)])
                            matched_partial = True
                            break
                    if matched_partial:
                        break
                    else:
                        thinking_out.append(remaining)
                        break
                        
        return "".join(thinking_out), "".join(standard_out)
