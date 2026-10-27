-- Legacy dashboard-created auth trigger used any enabled policy (LIMIT 1),
-- bypassing the explicit signup default and API bootstrap in CONTRACTS 21.34.
-- Keep its trigger function callable for existing installations, but let the
-- verified API request own profile/storage-account creation atomically.
-- Do not create an auth.users trigger on environments that do not have one.
CREATE OR REPLACE FUNCTION public.handle_new_pilot_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    RETURN NEW;
END;
$$;
