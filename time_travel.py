from graph import court_graph


def show_history(thread_id):
    config = {"configurable": {"thread_id": thread_id}}
    history = list(court_graph.get_state_history(config))

    for i, snapshot in enumerate(history):
        next_node = snapshot.next[0] if snapshot.next else "END (finished)"
        checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
        print(f"[{i}] next -> {next_node}   (checkpoint_id: {checkpoint_id})")

    return history


if __name__ == "__main__":
    thread_id = input("Enter thread_id to inspect (from a previous main.py run): ").strip()
    history = show_history(thread_id)

    if not history:
        print("No saved history found for this thread_id.")
    else:
        choice = input("\nEnter the [index] to fork from (or press Enter to skip): ").strip()
        if choice:
            snapshot = history[int(choice)]
            fork_config = snapshot.config  # pins to that exact past checkpoint

            print(f"\nResuming from checkpoint {fork_config['configurable']['checkpoint_id']}...")
            result = court_graph.invoke(None, config=fork_config)

            print("=" * 100)
            print("RESULT FROM FORKED RUN:")
            print(result.get("verdict", "(not reached yet — may need another resume if paused before judge)"))