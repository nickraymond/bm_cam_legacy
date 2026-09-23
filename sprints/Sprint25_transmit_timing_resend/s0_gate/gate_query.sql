-- S0 gate (A), read-only: rev 3 producers BMCAM_003/004, rows per UTC day of timestamp_utc.
select device_id, (timestamp_utc at time zone 'UTC')::date as day, type::text as type,
       count(*) as rows,
       count(*) filter (where is_complete) as complete,
       count(*) filter (where not coalesce(is_complete,false)) as partial,
       round(100.0*count(*) filter (where is_complete)/count(*)) as pct_complete
from media
where device_id in ('BMCAM_003','BMCAM_004') and timestamp_utc >= now() - interval '8 days'
group by 1,2,3 order by 1,2,3;
