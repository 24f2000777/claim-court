from graph import court_graph

png_bytes = court_graph.get_graph().draw_mermaid_png()

with open("graph.png", "wb") as f:
    f.write(png_bytes)

print("Saved graph.png")