def parallel_execute_with_rate_limit(
    func: Callable, 
    args_list: List[Tuple], 
    qps: float, 
    max_workers: int = None, 
    avg_task_duration_s: float = 6.0,
    max_retries: int = 4, 
    always_retry: bool = True, 
    backoff_factor: float = 1.0, # 新增：退避因子，用于控制退避时间
    desc: str = 'Tasks'
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    使用速率限制器和指数退避策略并发执行任务。

    :param func: 要执行的目标函数。
    :param args_list: 包含每个任务参数的列表。
    :param qps: 每秒请求数 (QPS) 限制。
    :param max_workers: 最大并发线程数。
    :param avg_task_duration_s: 估计的单个任务平均耗时（秒）。
    :param max_retries: 单个任务的最大重试次数。
    :param always_retry: 如果为True，则无限重试。
    :param backoff_factor: 指数退避的基础时间因子（秒）。
    :param desc: 进度描述。
    :return: 一个元组 (results, stats)。
    """
    if max_workers is None:
        calculated_workers = int(qps * avg_task_duration_s * 1.0)
        max_workers = max(int(qps) + 1, min(calculated_workers, 30))
    
    print(f"Total tasks: {len(args_list)}, Max workers: {max_workers}, QPS: {qps}")
    
    limiter = RateLimiter(qps=qps)
    total_tasks = len(args_list)
    results = [None] * total_tasks
    
    stats = {
        "total_tasks": total_tasks,
        "successful_tasks": 0,
        "permanent_failures": 0,
        "total_api_calls": 0,
        "total_retries": 0,
        "success_rate": 0.0,
        "failure_rate": 0.0,
    }
    stats_lock = threading.Lock()

    # --- 1. 定义一个包含重试逻辑的内部工作函数 ---
    def _task_worker(index: int, args: Tuple):
        """此函数负责单个任务的完整生命周期，包括速率限制和重试。"""
        retry_count = 0
        while True:
            try:
                # 在每次尝试前都获取令牌
                limiter.acquire()

                with stats_lock:
                    stats["total_api_calls"] += 1
                
                result = func(*args)

                # 任务成功条件
                if result is not None and (not isinstance(result, tuple) or (len(result) == 2 and result[0] is not None and result[1] is not None)):
                    with stats_lock:
                        stats["successful_tasks"] += 1
                    return index, result, None  # (index, 成功结果, 无错误)
                else:
                    # 结果无效，视为一种失败
                    raise ValueError("Task returned None or invalid result")

            except Exception as e:
                # 检查是否可以重试
                should_retry = always_retry or retry_count < max_retries
                if should_retry:
                    retry_count += 1
                    with stats_lock:
                        stats["total_retries"] += 1
                    
                    # --- 2. 实现指数退避 + 抖动 ---
                    # 等待时间 = backoff_factor * (2 ** (重试次数 - 1)) + 随机秒数
                    wait_time = backoff_factor * (2 ** (retry_count - 1)) + random.uniform(0, 0.5)
                    # print(f"Task {index} failed with {e}, retrying in {wait_time:.2f} seconds (attempt {retry_count})...")
                    time.sleep(wait_time)
                    # 继续下一次循环尝试
                    continue
                else:
                    # 达到最大重试次数，任务永久失败
                    print(f"Task {index} failed permanently after {max_retries} retries. Error: {e}")
                    with stats_lock:
                        stats["permanent_failures"] += 1
                    return index, None, e # (index, 无结果, 最终错误)

    # --- 3. 简化主执行逻辑 ---
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务，每个任务都由 _task_worker 管理
        future_to_index = {executor.submit(_task_worker, i, args): i for i, args in enumerate(args_list)}

        completed_count = 0
        next_report_milestone = 0.1

        # 使用 as_completed 来处理完成的任务，代码更简洁
        for future in concurrent.futures.as_completed(future_to_index):
            try:
                index, result, error = future.result()
                if error is None:
                    results[index] = result
                else:
                    # 可以在这里记录最终的错误信息
                    results[index] = None # 或者 error 对象
            except Exception as exc:
                # _task_worker 内部已经处理了所有预期异常，这里是意外情况
                index = future_to_index[future]
                print(f"Task {index} generated an unexpected exception: {exc}")
                with stats_lock:
                    stats["permanent_failures"] += 1

            # 更新进度
            completed_count += 1
            current_progress = completed_count / total_tasks
            if current_progress >= next_report_milestone:
                progress_percent = int(next_report_milestone * 100)
                print(f"[{desc}] Progress: {progress_percent}% complete ({completed_count}/{total_tasks} tasks).")
                while next_report_milestone <= current_progress:
                     next_report_milestone += 0.1

    if stats["total_tasks"] > 0:
        stats["success_rate"] = stats["successful_tasks"] / stats["total_tasks"]
        stats["failure_rate"] = stats["permanent_failures"] / stats["total_tasks"]

    return results, stats