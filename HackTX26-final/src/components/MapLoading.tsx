type Props = {
  studentsLoaded: boolean
  hasCaller: boolean
  embedded: boolean
}

export function MapLoading({ studentsLoaded, hasCaller, embedded }: Props) {
  return (
    <div className="map-loading" role="status" aria-live="polite">
      <div className="load-pin" aria-hidden />
      <p className="load-word">Loading</p>
      <h2>Distances stay hidden</h2>
      <p className="lede">
        Pins are drawn from the center once the caller has an embedding and the matching API returns their nearest students.
        Nothing on this side is a saved distance.
      </p>
      <ul className="load-wait">
        <li className={studentsLoaded ? 'in' : ''}>
          <i />
          Students loaded from the API
        </li>
        <li className={hasCaller ? 'in' : ''}>
          <i />
          A call is in
        </li>
        <li className={embedded ? 'in' : ''}>
          <i />
          Nearest students found
        </li>
      </ul>
    </div>
  )
}
