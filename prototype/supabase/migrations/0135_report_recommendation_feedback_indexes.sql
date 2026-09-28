create index if not exists report_recommendation_feedback_client_user_idx
  on public.report_recommendation_feedback(client_user_id,updated_at desc);

create index if not exists report_recommendation_feedback_team_updated_by_idx
  on public.report_recommendation_feedback(team_updated_by)
  where team_updated_by is not null;
